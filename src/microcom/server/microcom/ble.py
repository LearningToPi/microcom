# pylint: disable=W0401
import asyncio
import gc
import os
import sys
from gc import collect
from time import time
from bluetooth import BLE # pyright: ignore[reportMissingImports]
from micropython import const  # type: ignore # pylint: disable=E0401
import libs.aioble as aioble
from libs.aioble.device import DeviceConnection
from microcom.server import MicrocomServer
from microcom.msg._base import MicrocomMsg, SER_START_HEADER, SER_END, DIR_REPLY_FRAG, TEXT_ENCODING, MSG_TYPE_STATS
from microcom.async_lock import AsyncLock
from microcom.exceptions import *
from microcom.log_manager import DEBUG

try:
    import bluetooth # type: ignore # pylint: disable=E0401
except:
    raise MicrocomUnsupported(f"Platform {os.uname().nodename} is not recognized by the Microcom Bluetooth LE server module.")


# Bluetooth IO capabilities
_IO_CAPABILITY_DISPLAY_ONLY = const(0)
_IO_CAPABILITY_DISPLAY_YESNO = const(1)
_IO_CAPABILITY_KEYBOARD_ONLY = const(2)
_IO_CAPABILITY_NO_INPUT_OUTPUT = const(3)
_IO_CAPABILITY_KEYBOARD_DISPLAY = const(4)
IO_CAPABILITY_TEXT = ('display_code', 'display_yes_no', 'keyboard_only', 'no_input_output', 'keyboard_display')

# BLE service defaults
MICROCOM_BLE_NAME = "Microcom BLE Server"
BLE_ADVERTISE_INTERVAL_MS = 100
BLE_MITM_ENABLED = True
BLE_BOND_ENABLED = True
BLE_SECURE_ENABLED = True
BLE_IO_MODE = 'display_code'
BLE_BUFFER_LEN = 256
BLE_SECRETS_PATH = 'ble_secrets.json'

# Service definition UUID's
_UART_SERVICE = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_TX_CHARACTERISTIC = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E") # Write from server to client
_TX_READ_CHARACTERISTIC = bluetooth.UUID("8d970003-4a06-41c9-91a8-88836a1531cd") # Client writes ID of last message read
_RX_CHARACTERISTIC = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E") # Read from client to server
_RX_READ_CHARACTERISTIC = bluetooth.UUID("8d970002-4a06-41c9-91a8-88836a1531cd") # Server writes ID of last message read


class MicrocomServerBLE(MicrocomServer):
    ''' Class to represent a Bluetooth LE UART server using the Nordic UART Service '''
    def __init__(self, name:str=MICROCOM_BLE_NAME, advertise_interval_ms:int=BLE_ADVERTISE_INTERVAL_MS, 
                 mitm_enabled:bool=BLE_MITM_ENABLED, bond_enabled:bool=BLE_BOND_ENABLED, secure_enabled:bool=BLE_SECURE_ENABLED,
                 io_mode:str=BLE_IO_MODE, buffer_len:int=BLE_BUFFER_LEN, secrets_path:str=BLE_SECRETS_PATH, **kwargs):
        super().__init__(**kwargs)
        self.name, self._name_bytes, self._advertise_interval_us = name, name.encode('utf-8'), advertise_interval_ms * 1000
        self.mitm, self.bond, self.le_secure, self.io_mode, self.buffer_len = mitm_enabled, bond_enabled, secure_enabled, io_mode, buffer_len
        self.secrets_path = secrets_path
        self.active_connection = None
        self.__ble_send_lock = AsyncLock()

        self._logger.info(f"{self.__i} Starting server with MITM: {self.mitm}, Bond: {self.bond}, LE Secure {self.le_secure}, IO Mode: {self.io_mode} ...")

        # configure BLE service
        aioble.config(gap_name=self.name)

        # register the GATT server
        self._logger.info(f"{self.__i} Registering BLE GATT services")
        self.uart_service = aioble.Service(_UART_SERVICE)
        self.tx_characteristic = aioble.BufferedCharacteristic(service=self.uart_service, uuid=_TX_CHARACTERISTIC, read=True, write=False, notify=True, max_len=self.buffer_len, append=True)
        self.tx_read_characteristic = aioble.BufferedCharacteristic(service=self.uart_service, uuid=_TX_READ_CHARACTERISTIC, read=False, write=True, notify=False, max_len=16, append=False)
        self.rx_characteristic = aioble.BufferedCharacteristic(service=self.uart_service, uuid=_RX_CHARACTERISTIC, read=False, write=True, notify=False, max_len=self.buffer_len, append=True)
        self.rx_read_characteristic = aioble.BufferedCharacteristic(service=self.uart_service, uuid=_RX_READ_CHARACTERISTIC, read=True, write=False, notify=True, max_len=16, append=False)
        aioble.register_services(self.uart_service)

        # start receive and advertise loops
        self._receive_message_task = asyncio.create_task(self._receive_ack_thread())
        self._receive_message_task = asyncio.create_task(self._receive_thread())
        self._advertise_loop_task = asyncio.create_task(self._advertise_loop())

    @property
    def __i(self):
        ''' Return the info string for logging purposes '''
        return f"BLE {self.name}:"

    async def _advertise_loop(self):
        ''' Continuously advertise the device for peers '''

        while True:
            self._logger.info(f"{self.__i} Starting BLE Advertising...")
            await asyncio.sleep(.01)
            collect()
            try:
                self.active_connection = None
                async with await aioble.advertise(
                    interval_us=self._advertise_interval_us,
                    connectable=True,
                    name=self._name_bytes,
                    timeout_ms=None,
                    services=[_UART_SERVICE]
                ) as connection: # pyright: ignore[reportOptionalContextManager]
                    self._logger.info(f"{self.__i} BLE connection started for: {connection.device}")
                    await asyncio.sleep(.01)
                    self.active_connection = connection
                    await self.active_connection.pair()
                    await connection.disconnected(timeout_ms=None)
                    self._logger.info(f"{self.__i} device {connection.device} is_connected(): {connection.is_connected()}")
            except asyncio.CancelledError:
                self._logger.info(f"{self.__i} Stopped BLE Advertising.")
                return
            except Exception as e:
                self._logger.error(f"{self.__i} Advertise loop error: {e.__class__.__name__}: {e}...")

    async def send_ack(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None):
        ''' Send ACK messages - ACK is handled during receiving for Bluetooth LE '''
        pass

    async def send(self, message, timeout:None|int=None, retry:None|int=None, wait_for_ack:bool=True, wait_for_reply:bool=False, data=None):
        ''' Send a message back using the tx service '''
        if self.active_connection is not None:
            timeout = timeout if timeout is not None else self.send_timeout
            retry = retry if retry is not None else self.retry
            if data is not None:
                message.data = data # if data was passed to the function, fill it in

            if message.data_length > self.buffer_len:
                # if we need to fragment, break up the packet and send
                frag_count = 1
                fragment = None
                for fragment in message.frag(self.buffer_len - (message.total_length - message.data_length)):
                    self._logger.debug(f"{self.__i} SEND fragment {frag_count} for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) size: {fragment.data_length}")
                    await self.send(fragment, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply if message.direction != DIR_REPLY_FRAG else False)
                    if not fragment.ack_received():
                        self._logger.error(f"{self.__i} SEND fragment  {frag_count} for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) failed.")
                        del fragment
                        gc.collect()
                        return
                    frag_count += 1
                # clean up memory to ensure we don't overload
                del fragment
                gc.collect()
                return

            async with self.__ble_send_lock:
                try:
                    if wait_for_ack or wait_for_reply:
                        self._last_sent_message = message
                    message.send_time = time()
                    header, data, footer = message.serialize()

                    self._logger.debug(f"{self.__i} Sending message: {message.ip} {header}")
                    self.tx_characteristic.write(header + data + footer, send_update=True)
                except Exception as e:
                    self._logger.error(f"{self.__i} Error sending message: {message}: {e.__class__.__name__}: {e}")
                    return

                # Must ALWAYS wait for ACK on BLE to prevent overwriting a previous message
                #if not wait_for_ack:
                #    return

                while not message.ack_received() and time() < (message.send_time + timeout): # type: ignore
                    await asyncio.sleep(.1)
                if message.ack_received(): # type: ignore
                    if wait_for_ack and not wait_for_reply:
                        return
                    # if we are waiting for a reply
                    while not message.reply_received() and time() < (message.send_time + timeout): # type: ignore
                        await asyncio.sleep(.1)
                    return # return regardless of if a reply was received

            # if we didn't get an ACK
            self._logger.warning(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Send timeout after {timeout} seconds waiting for ACK. Retry {message.retries} / {retry}") # type: ignore
            self._logger.debug(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Send timeout packet details: {bytes(message.data_bytes[0:100])}") # type: ignore
            if message.retries < retry: # type: ignore
                # increment the retry counter and call the send function again
                message.retries += 1 # type: ignore
                return await self.send(message=message, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply)
            else:
                self._logger.error(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Retry exceeded with no ACK. Message discarded.") # type: ignore

        else:
            self._logger.warning(f"{self.__i} No BLE connection active to send ACK for Message type: {message.msg_type}, id: {message.pkt_id}")

    async def _receive_ack_thread(self):
        ''' Thread to process ACK messages from the TX_READ characteristic (client successfully read the message) '''
        self._logger.info(f"{self.__i} Starting ACK receive loop...")
        await asyncio.sleep(.01)
        while True:
            try:
                gc.collect()
                await self.tx_read_characteristic.written()
                message_id = int(self.tx_read_characteristic.read().decode('utf-8'))
                if self._last_sent_message.pkt_id == message_id:
                    self._last_sent_message.ack_time = time()
                    self._logger.debug(f"{self.__i} Received ACK for message ID: {message_id}")
                else:
                    self._logger.warning(f"{self.__i} Received ACK for message ID: {message_id}, does not match last sent packet id: {self._last_sent_message.pkt_id if self._last_sent_message is not None else None}")
            except Exception as e:
                self._logger.error(f"{self.__i} Error in receive ACK thread: {e.__class__.__name__}: {e}")
                if self._logger.console_level == 7:
                    sys.print_exception(e) # pyright: ignore[reportAttributeAccessIssue] # pylint: disable=E1101

    async def _receive_thread(self):
        self._logger.info(f"{self.__i} Starting receive loop...")
        data = b''
        await asyncio.sleep(.01)
        while True:
            try:
                gc.collect()
                await self.rx_characteristic.written()
                data += self.rx_characteristic.read()
                self._logger.debug(f"{self.__i} Received BLE data: {data}")
                await asyncio.sleep(.01)

                # check if start and end values present
                if SER_START_HEADER in data and SER_END in data:
                    received_msg = MicrocomMsg.from_bytes(data, (self.active_connection.device.addr if self.active_connection is not None else None,))
                    data = b''
                    self._logger.debug(f"{self.__i} Received message: {received_msg}")
                    # set the last read message ID in the RX_READ characteristic so the client knows the read is complete
                    self._logger.debug(f"{self.__i} Sending ACK for message ID: {received_msg.pkt_id}")
                    self.rx_read_characteristic.write(str(received_msg.pkt_id).encode('utf-8'), send_update=True)
                    asyncio.create_task(self._receive_message(received_msg))

                # make sure that the data starts with the start header
                #data = data[data.find(SER_START_HEADER):]

                # If the start header shows up a second time, drop everything before the 2nd start header (incomplete packet)
                if SER_START_HEADER in data[len(SER_START_HEADER):]:
                    data = data[data.find(SER_START_HEADER, len(SER_START_HEADER))]

            except asyncio.CancelledError:
                self._logger.info(f"{self.__i} Stopping receive loop")
            except Exception as e:
                self._logger.error(f"Error in receive loop: {e.__class__.__name__}: {e}")
                if self._logger.console_level == 7:
                    sys.print_exception(e) # pyright: ignore[reportAttributeAccessIssue] # pylint: disable=E1101
