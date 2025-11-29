# pylint: disable=W0401
import asyncio
import os
from time import time
import gc
from microcom.server import MicrocomServer
from microcom.msg._base import MicrocomMsg, SER_START_HEADER, SER_END, DIR_REPLY_FRAG, MSG_TYPE_STATS
from microcom.async_lock import AsyncLock
from microcom.exceptions import MicrocomUnsupported

try:
    import libs.aioble as aioble # type: ignore # pylint: disable=import-error
except Exception as e:
    raise MicrocomUnsupported(f"Platform {os.uname().nodename} is not recognized by the Microcom BLE L2CAP server module.") from e

# BLE Configuration Constants
BLE_CONNECT_TIMEOUT = 10
BLE_ADV_INTERVAL_MS = 100
BLE_L2CAP_PSM = 0x0001 #0x80  # Dynamic PSM for L2CAP
BLE_L2CAP_MTU = 512
DATA_BLE_TIMEOUT = 1
MAX_MSG_LENGTH = 384
DATA_MAX_BUFFER_SIZE = 801
BLE_POLLING_INTERVAL = 0.05
BLE_MAX_CONNECTIONS = 4
BLE_INIT_RETRY_DELAY = 10  # Wait 10 seconds between init attempts
BLE_INIT_STARTUP_DELAY = 3  # Wait 3 seconds before first advertise attempt
BLE_MAX_RETRY_DELAY = 30  # Maximum wait between retries

# BLE Advertisement flags
BLE_ADV_FLAGS = 0x06  # LE General Discoverable Mode, BR/EDR Not Supported


class MicrocomServerL2CAP(MicrocomServer):
    """BLE L2CAP server using aioble for MicroPython.

    - Advertises a device name
    - Accepts incoming BLE connections
    - Accepts an L2CAP server channel on a PSM and exchanges serialized Microcom messages
    """

    def __init__(self, name: str, psm: int = BLE_L2CAP_PSM, mtu: int = BLE_L2CAP_MTU,
                 timeout=DATA_BLE_TIMEOUT, max_input_len: int = MAX_MSG_LENGTH, **kwargs):
        super().__init__(send_timeout=timeout, **kwargs)
        self._name = name
        self._psm = psm
        self._mtu = mtu
        self._max_input_len = max_input_len

        # BLE state
        self._advertiser = None
        self._connections = {}  # conn_handle -> dict(connection, l2cap, peer)

        # Locks
        self.__ble_write_lock = AsyncLock()
        self.__ble_send_lock = AsyncLock()

        self._logger.info(f"Starting BLE L2CAP server '{self._name}' (PSM {self._psm})...")

        # BLE initialization is handled by aioble internally via ensure_active()
        # No need to manually activate the controller here

        # Start tasks
        self._adv_task = asyncio.create_task(self._advertise_loop())

    def close(self):
        self._logger.info("Closing Microcom BLE L2CAP Server")
        # Cancel tasks
        try:
            if self._adv_task:
                self._adv_task.cancel()
        except Exception:
            pass

        # Disconnect and cleanup connections
        for handle, info in list(self._connections.items()):
            try:
                if info.get("l2cap"):
                    info["l2cap"].close()
            except Exception:
                pass
            try:
                if info.get("connection"):
                    info["connection"].close()
            except Exception:
                pass
            del self._connections[handle]

        # Stop advertising
        try:
            if self._advertiser:
                self._advertiser.stop()
        except Exception:
            pass

        super().close()

    async def _advertise_loop(self):
        """Continuously advertise the device so peers can connect."""
        self._logger.info(f"Advertising BLE device '{self._name}'")
        first_attempt = True
        retry_delay = BLE_INIT_STARTUP_DELAY
        consecutive_errors = 0

        while True:
            try:
                # Wait longer on first attempt to let controller stabilize
                if first_attempt:
                    self._logger.info(f"Waiting {BLE_INIT_STARTUP_DELAY}s for BLE controller to stabilize...")
                    await asyncio.sleep(BLE_INIT_STARTUP_DELAY)
                    first_attempt = False
                    # Force garbage collection to free resources
                    gc.collect()
                
                async with self.__ble_write_lock:
                    # Use the name parameter instead of manually building adv_data
                    # aioble.advertise blocks until a connection arrives
                    # Encode name as bytes for aioble
                    name_bytes = self._name.encode("utf-8") if isinstance(self._name, str) else self._name
                    self._logger.debug("Calling aioble.advertise()...")
                    connection = await aioble.advertise(
                        interval_us=BLE_ADV_INTERVAL_MS * 1000,
                        connectable=True,
                        name=name_bytes,
                        timeout_ms=None  # No timeout, wait indefinitely for connection
                    )
                    self._logger.debug(f"advertise() returned connection: {connection}")
                    # Reset error counter on success
                    consecutive_errors = 0
                    retry_delay = BLE_INIT_RETRY_DELAY
                    # Create a task to handle this connection
                    asyncio.create_task(self._handle_connection(connection))

            except asyncio.CancelledError:
                self._logger.info("BLE advertisement cancelled")
                break
            except OSError as e:
                # OSError 116 = ETIMEDOUT - BLE controller initialization timeout
                consecutive_errors += 1
                self._logger.warning(f"BLE OSError (attempt {consecutive_errors}): {e}")
                # Use exponential backoff: 10s, 20s, 30s (capped at max)
                retry_delay = min(BLE_INIT_RETRY_DELAY * consecutive_errors, BLE_MAX_RETRY_DELAY)
                self._logger.info(f"Retrying in {retry_delay}s...")
                # Force garbage collection before retry
                gc.collect()
                await asyncio.sleep(retry_delay)
            except Exception as e:
                # Generic error handling
                consecutive_errors += 1
                self._logger.error(f"BLE advertise error (attempt {consecutive_errors}): {e.__class__.__name__}: {e}")
                retry_delay = min(BLE_INIT_RETRY_DELAY * consecutive_errors, BLE_MAX_RETRY_DELAY)
                self._logger.info(f"Retrying in {retry_delay}s...")
                # Force garbage collection before retry
                gc.collect()
                await asyncio.sleep(retry_delay)

    async def _handle_connection(self, connection):
        """Handle a single incoming BLE connection."""
        conn_handle = id(connection)
        peer = None
        try:
            try:
                peer = connection.peer_address
            except Exception:
                peer = None

            self._logger.info(f"BLE connection established handle={conn_handle}, peer={peer}")
            
            # Try to accept an L2CAP server on the PSM using aioble's API
            # DeviceConnection exposes `l2cap_accept(psm, mtu)` which returns
            # an L2CAP channel object with `recv`/`send` methods. If unavailable
            # or the accept fails, close the connection (we don't support
            # GATT/characteristic-based fallback here).
            l2cap = None
            try:
                l2cap = await connection.l2cap_accept(self._psm, self._mtu)
                self._logger.info(f"Accepted L2CAP server on PSM {self._psm} for handle={conn_handle}")
            except AttributeError as e:
                self._logger.debug(f"DeviceConnection.l2cap_accept not available; closing connection: {e.__class__.__name__}: {e}")
            except Exception as e:
                self._logger.debug(f"L2CAP accept failed ({e}); closing connection handle={conn_handle}")
                try:
                    # Attempt a clean disconnect if possible
                    await connection.disconnect()
                except Exception:
                    pass
                return

            # Store connection
            self._connections[conn_handle] = {"connection": connection, "l2cap": l2cap, "peer": peer, "connect_time": time()}

            # Handle incoming data on this connection
            await self._connection_receive_loop(conn_handle)

        finally:
            # Cleanup
            self._logger.info(f"BLE connection closed: {conn_handle}")
            info = self._connections.pop(conn_handle, None)
            try:
                if info:
                    if info.get("l2cap"):
                        info["l2cap"].close()
            except Exception:
                pass
            try:
                if info and info.get("connection"):
                    info["connection"].close()
            except Exception:
                pass
            gc.collect()

    async def _connection_receive_loop(self, conn_handle: int):
        """Receive loop for a specific connection: reads from L2CAP if available otherwise from the connection."""
        info = self._connections.get(conn_handle)
        if not info:
            return
        l2cap = info.get("l2cap")

        # If there's no L2CAP channel we can't `recv`/`send` on the DeviceConnection
        # object itself. Abort the receive loop in that case.
        if not l2cap:
            self._logger.warning(f"No L2CAP channel for handle={conn_handle}; aborting receive loop")
            return

        # Read loop using the L2CAP channel
        while True:
            try:
                data = await l2cap.recv(self._mtu)

                if not data:
                    # closed or no data
                    break

                self._logger.debug(f"RECEIVE received {len(data)} bytes from handle={conn_handle}")
                if SER_START_HEADER in data and SER_END in data:
                    # Provide a tuple source where first element is connection handle
                    received_msg = MicrocomMsg.from_bytes(data, (conn_handle, None))
                    asyncio.create_task(self._receive_thread(received_msg))

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"Connection receive error handle={conn_handle}: {e}")
                break

    async def send_ack(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None):
        """Send ACK for a message back to the peer that sent it.

        For BLE we reuse the Microcom message fields: `ip` holds the connection handle, `port` is None.
        """
        if message.ip is None:
            self._logger.error("Unable to send ACK: no connection handle in message.ip")
            return
        conn_handle = message.ip
        info = self._connections.get(conn_handle)
        if not info:
            self._logger.error(f"SEND_ACK: connection {conn_handle} not found")
            return

        header, data, footer = MicrocomMsg.ack(message=message).serialize()
        payload = header + data + footer

        async with self.__ble_write_lock:
            try:
                if info.get("l2cap"):
                    await info["l2cap"].send(payload)
                else:
                    await info["connection"].send(payload)
            except Exception as e:
                self._logger.error(f"SEND_ACK error handle={conn_handle}: {e}")

    async def send(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None, wait_for_ack=True, wait_for_reply=False, data=None):
        """Send a MicrocomMsg over BLE L2CAP.

        `message.ip` should contain the target connection handle (int) and `message.port` is ignored.
        """
        timeout = timeout if timeout is not None else self.send_timeout
        retry = retry if retry is not None else self.retry
        if data is not None:
            message.data = data

        # Support streaming stats when configured (stream_stats_client is expected to be a (handle, None) tuple)
        if message.msg_type == MSG_TYPE_STATS:
            if isinstance(self._stream_stats_client, tuple) and len(self._stream_stats_client) == 2:
                message.ip, message.port = self._stream_stats_client[0], self._stream_stats_client[1]
            else:
                self._logger.warning("SEND streaming stats enabled but no destination. Discarding stats packets.")
                return

        conn_handle = message.ip
        if conn_handle not in self._connections:
            self._logger.error(f"Unable to send data: connection {conn_handle} not found")
            return

        # Fragment if necessary
        if message.data_length > DATA_MAX_BUFFER_SIZE:
            frag_count = 1
            for fragment in message.frag(DATA_MAX_BUFFER_SIZE):
                self._logger.debug(f"Sending fragment {frag_count} for pkt_id={fragment.pkt_id} to handle={conn_handle} size={fragment.data_length}")
                await self.send(fragment, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack,
                                wait_for_reply=(wait_for_reply if message.direction != DIR_REPLY_FRAG else False))
                if not fragment.ack_received():
                    self._logger.error(f"Fragment {frag_count} failed for pkt_id={fragment.pkt_id} to handle={conn_handle}")
                    del fragment
                    gc.collect()
                    return
                frag_count += 1
            del fragment
            gc.collect()
            return

        # Prepare payload
        if wait_for_ack or wait_for_reply:
            self._last_sent_message = message
        message.send_time = time()
        header, data_payload, footer = message.serialize()
        payload = header + data_payload + footer

        async with self.__ble_send_lock:
            info = self._connections.get(conn_handle)
            if not info:
                self._logger.error(f"SEND: connection {conn_handle} not found")
                return

            async with self.__ble_write_lock:
                try:
                    if info.get("l2cap"):
                        await info["l2cap"].send(payload)
                    else:
                        await info["connection"].send(payload)
                except Exception as e:
                    self._logger.error(f"SEND error handle={conn_handle}: {e}")
                    return

            if not wait_for_ack:
                return

            # Wait for ACK
            while not message.ack_received() and time() < (message.send_time + timeout):
                await asyncio.sleep(0.1)

            if message.ack_received():
                if wait_for_ack and not wait_for_reply:
                    return
                while not message.reply_received() and time() < (message.send_time + timeout):
                    await asyncio.sleep(0.1)
                return

        # If no ACK received, retry
        self._logger.warning(f"ID: {message.pkt_id} to handle={conn_handle}, Type: {message.msg_type}, Send timeout after {timeout}s waiting for ACK. Retry {message.retries}/{retry}")
        if message.retries < retry:
            message.retries += 1
            return await self.send(message=message, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply)
        else:
            self._logger.error(f"ID: {message.pkt_id} to handle={conn_handle}, Type: {message.msg_type}, Retry exceeded with no ACK. Message discarded.")

    def send_hello(self):
        """Optional hello logic to announce features (no-op here)."""
        return


# Mark progress
try:
    from microcom.server import MicrocomServer as _M
except Exception:
    _M = None

