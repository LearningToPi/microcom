# pylint: disable=W0401
import asyncio
import os
from time import time
import gc
import sys
from socket import socket, AF_INET, SOCK_DGRAM, getaddrinfo, SOL_SOCKET, SO_REUSEADDR
from select import poll, POLLIN
import binascii
from microcom.server import MicrocomServer
from microcom.msg._base import MicrocomMsg, SER_START_HEADER, SER_END, DIR_REPLY_FRAG, TEXT_ENCODING, MSG_TYPE_STATS
from microcom.async_lock import AsyncLock
from microcom.exceptions import *
from microcom.log_manager import DEBUG

try:
    import network # type: ignore # pylint: disable=import-error
except:
    raise MicrocomUnsupported(f"Platform {os.uname().nodename} is not recognized by the Microcom WIFI UDP server module.")

WIFI_MODE_MAPPING = {'station': network.WLAN.IF_STA, 'ap': network.WLAN.IF_AP}
WIFI_MODE_DEFAULT = 'station'
# load available security modes

WIFI_SEC_MODE_MAPPING = {}
for sec_mode in [x for x in dir(network.WLAN) if x.startswith('SEC_')]:
    WIFI_SEC_MODE_MAPPING[sec_mode.replace('SEC_', '').lower()] = sec_mode

WIFI_PREFERENCE_ORDER = ['wpa3', 'wpa2_wpa3', 'wpa2', 'wpa_wpa2']
WIFI_SEC_MODE_DEFAULT = 'wpa2' # set the default to wpa2, then search for a more preferred
for preference in WIFI_PREFERENCE_ORDER:
    if preference in WIFI_MODE_MAPPING:
        WIFI_SEC_MODE_DEFAULT = preference
        break

#WIFI_SEC_MODE_MAPPING = {'wpa2': network.WLAN.SEC_WPA2, 'wpa2-3': network.WLAN.SEC_WPA2_WPA3, 'wpa3': network.WLAN.SEC_WPA3, 'wpa2_ent': network.WLAN.SEC_WPA2_ENT}
WIFI_CONNECT_TIMEOUT = 10

# load available power save modes
WIFI_POWERSAVE_MAPPING = {}
for power_mode in [x for x in dir(network.WLAN) if x.startswith('PM_')]:
    WIFI_POWERSAVE_MAPPING[power_mode.replace('PM_', '').lower()] = power_mode

WIFI_POWERSAVE_MODE_DEFAULT = 'powersave' if 'powersave' in WIFI_POWERSAVE_MAPPING else network.WLAN.PM_NONE

# Load status messages
WIFI_STATUS_MESSAGES = {}
for status_msg in [x for x in dir(network) if x.startswith('STAT_')]:
    WIFI_STATUS_MESSAGES[getattr(network, status_msg)] = status_msg.replace('STAT_', '')

UDP_PORT = 2099
DATA_WIFI_TIMEOUT = 1
MAX_MSG_LENGTH = 384
DATA_MAX_BUFFER_SIZE = 801
WIFI_POLLING_INTERVAL = .1


class MicrocomServerUDP(MicrocomServer):
    ''' Class to represent a serial connected client '''
    def __init__(self, ssid:str, key:list, port:int=UDP_PORT, mode:str=WIFI_MODE_DEFAULT, security_mode:str=WIFI_SEC_MODE_DEFAULT,
                 powersave_mode:str=WIFI_POWERSAVE_MODE_DEFAULT, timeout=DATA_WIFI_TIMEOUT, max_input_len=MAX_MSG_LENGTH, **kwargs):
        super().__init__(send_timeout=timeout, **kwargs)
        self._ssid, self._key, self._mode, self._security_mode, self._wlan = ssid, key, mode, security_mode, network.WLAN(WIFI_MODE_MAPPING[mode])
        self._powersave_mode = powersave_mode
        self._port, self._server = port, None
        self.__wifi_write_lock = AsyncLock()
        self.__wifi_send_lock = AsyncLock()
        self._max_input_len = max_input_len
        self._logger.info("Starting WIFI...")
        # start by disconnecting and deactivating the wireless
        try:
            self._wlan.disconnect()
            self._wlan.active(False)
        except:
            pass
        # start the wifi connect routine
        self._wifi_task = asyncio.create_task(self.wifi_connect())
        self._receive_task = asyncio.create_task(self._receive_thread())
        # set the wlan object in the monitor so we can pull stats
        self._monitor.wlan = self._wlan

    def close(self):
        self._logger.info("Closing Microcom UDP Server")
        super().close()
        self._wlan.disconnect()
        self._wlan.active(False)

    async def wifi_connect(self):
        ''' Reconfigure the serial port using the provided parameters '''
        self._logger.info(f"Connecting wifi as {self._mode}, ssid: {self._ssid}, security mode: {self._security_mode}, power mode: {self._powersave_mode}"
                          f"mac: {binascii.hexlify(self._wlan.config('mac')).decode()}")
        await asyncio.sleep(.01)
        if self._wlan.isconnected():
            self._logger.info(f"WIFI already connected to SSID {self._ssid}, IP config: {self._wlan.ipconfig('addr4')}")
        while True:
            try:
                async with self._wifi_lock:
                    if not self._wlan.active():
                        self._logger.info("Activating WIFI adapter...")
                        await asyncio.sleep(.01)
                        self._wlan.active(True)
                    if not self._wlan.isconnected():
                        if self._security_mode not in ['wpa2_ent']:
                            self._logger.info(f"WIFI attempting to connect to {self._ssid}...")
                            await asyncio.sleep(.01)
                            self._wlan.disconnect()
                            self._wlan.config(reconnects=-1,
                                              pm=getattr(network.WLAN, WIFI_POWERSAVE_MAPPING.get(self._powersave_mode, 'none')))
                            self._wlan.connect(self._ssid,
                                               self._key)
                        else:
                            self._logger.error("WPA2 Enterprise is not currently supported.  Reconfigure for WPA2/WPA3 PSK.")
                            await asyncio.sleep(.01)
                        for _ in range(10):
                            await asyncio.sleep(WIFI_CONNECT_TIMEOUT / 10)
                            if self._wlan.isconnected():
                                self._logger.info(f"WIFI connected to SSID {self._ssid}, IP config: {self._wlan.ipconfig('addr4')}")
                                await asyncio.sleep(.01)
                                break
                if not self._wlan.isconnected():
                    self._logger.error(f"WIFI error connecting: {WIFI_STATUS_MESSAGES[self._wlan.status()]}")
                    await asyncio.sleep(.01)
                await asyncio.sleep(WIFI_CONNECT_TIMEOUT)
            except Exception as e:
                self._logger.error(f"WIFI_CONNECT Error occured: {e.__class__.__name__}: {e}")
                if self._logger.console_level == DEBUG:
                    sys.print_exception(e)
                await asyncio.sleep(.01)

    async def send_ack(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None):
        ''' Send ACK messages as soon as the port is writable '''
        if message.ip is None or message.port is None:
            self._logger.error(f"Unable to send ACK to ({message.ip}:{message.port}). Discarding.")
            return
        async with self.__wifi_write_lock:
            header, data, footer = MicrocomMsg.ack(message=message).serialize()
            self._logger.debug(f"Sending ACK: ({message.ip}:{message.port}) {header}")
            if isinstance(self._server, socket):
                self._server.sendto(header + data + footer, (message.ip, message.port))
            else:
                self._logger.error("SEND_ACK: server object not initialized! Unable to send. Message discarded.")

    async def send(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None, wait_for_ack=True, wait_for_reply=False, data=None):
        ''' Function to send a message '''
        timeout = timeout if timeout is not None else self.send_timeout
        retry = retry if retry is not None else self.retry
        if data is not None:
            message.data = data # if data was passed to the function, fill it in

        # if this is a stats message, and we have a stats client defined, update the destination so we can send it
        if message.msg_type == MSG_TYPE_STATS and isinstance(self._stream_stats_client, tuple):
            message.ip, message.port = self._stream_stats_client[0], self._stream_stats_client[1]
        elif message.msg_type == MSG_TYPE_STATS:
            self._logger.warning("SEND streaming stats is enabled but no destination. Discarding stats packets.")
            return

        if message.data_length > DATA_MAX_BUFFER_SIZE:
            # if we need to fragment, break up the packet and send
            frag_count = 1
            fragment = None
            for fragment in message.frag(DATA_MAX_BUFFER_SIZE):
                self._logger.debug(f"SEND fragment {frag_count} for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) size: {fragment.data_length}")
                await self.send(fragment, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply if message.direction != DIR_REPLY_FRAG else False)
                if not fragment.ack_received():
                    self._logger.error(f"SEND fragment  {frag_count} for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) failed.")
                    del fragment
                    gc.collect()
                    return
                frag_count += 1
            # clean up memory to ensure we don't overload
            del fragment
            gc.collect()
            return

        # lock the writer to block any other send functon
        async with self.__wifi_send_lock:
            if wait_for_ack or wait_for_reply:
                self._last_sent_message = message
            message.send_time = time()
            header, data, footer = message.serialize()

            # lock the write to the wifi
            if message.ip is None or message.port is None:
                self._logger.error(f"Unable to send data to ({message.ip}:{message.port}). Discarding.")
                return
            async with self.__wifi_write_lock:
                self._logger.debug(f"Sending data to ({message.ip}:{message.port}): {len(header) + len(data) + len(footer)} bytes, {header} {data[0:100]}{'...' if len(data)> 100 else ''}")
                if isinstance(self._server, socket):
                    self._server.sendto(header + data + footer, (message.ip, message.port))
                else:
                    self._logger.error("SEND_ACK: server object not initialized! Unable to send. Message discarded.")

            # if we aren't waiting for an ACK, then we don't need to retrans
            if not wait_for_ack:
                return

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

    def send_hello(self):
        ''' Send a hello to initialize the connection with supported parameters '''

    async def _receive_thread(self):
        ''' background thread to receive '''
        while True:
            try:
                # setup server socket, poller and buffer
                self._logger.info(f"Starting receive thread, listening on UDP port {self._port}...")
                async with self.__wifi_write_lock:
                    self._server = socket(AF_INET, SOCK_DGRAM)
                    self._server.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
                    self._server.bind(('', self._port))
                    self._server.setblocking(False)
                poller = poll()
                poller.register(self._server, POLLIN)
                self._logger.info(f"Listening for UDP connections on port {self._port}...")

                # loop to receive incoming data
                while True:
                    if poller.poll(1): # 1ms timeout checking if pending data
                        data, source = self._server.recvfrom(1500) # type: ignore # pylint: disable=E1101
                        self._logger.debug(f"RECEIVE_THREAD received {len(data)} bytes from {source}, bytes: {data}")

                        if SER_START_HEADER in data and SER_END in data:
                            # if we have the start and end, create a message to process
                            received_msg = MicrocomMsg.from_bytes(data, source)
                            self._logger.debug(f"RECEIVE_THREAD Message details: {received_msg}")
                            asyncio.create_task(self._receive_message(received_msg))
                    await asyncio.sleep(WIFI_POLLING_INTERVAL) # async sleep to allow other threads

            except Exception as e:
                self._logger.error(f"RECEIVE_THREAD error: {e.__class__.__name__}: {e}")
