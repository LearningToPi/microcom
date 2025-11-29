''' Bluetooth LE based Client '''
from typing import Callable
from logging_handler import INFO
from serial import Serial
from time import time, sleep
from src.microcom.client._base import MicrocomClient, RECEIVE_QUEUE_MAX, RETRY, SEND_QUEUE_MAX, SEND_TIMEOUT, SERVER_NAME, STATS_HISTORY, STATS_INTERVAL, STATS_STREAMING, LOG_LEVEL
from src.microcom.msg._base import *
from src.microcom.exceptions import *
import re
import asyncio
import traceback
from threading import Thread, Lock
from bleak import BleakClient, BleakScanner
from bleak.backends.service import BleakGATTServiceCollection
from bleak.backends.characteristic import BleakGATTCharacteristic

from microcom.msg._base import MicrocomMsg


# Microcom BLE defaults
BLE_DEVICE_NAME = 'Microcom Server'
BLE_CONNECT_TIMEOUT = 10
BLE_PAIR_DEVICE = True
BLE_RE_MAC = r'^[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}$'
BLE_GATT_CHAR_MAX_WRITE_LENGTH = 256
BLE_GATT_CHAR_RX_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
BLE_GATT_CHAR_TX_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
BLE_CONNECT_THREAD_TIMEOUT = 10


class MicrocomClientBLE(MicrocomClient):
    ''' Microcom BLE client '''
    def __init__(self, ble_device:str=BLE_DEVICE_NAME, ble_max_write_length:int=BLE_GATT_CHAR_MAX_WRITE_LENGTH, ble_connect_timeout:int=BLE_CONNECT_TIMEOUT,
                 ble_pair:bool=BLE_PAIR_DEVICE,
                 receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        self.ble_device, self.ble_max_write_length, self.ble_connect_timeout, self.ble_pair = ble_device, ble_max_write_length, ble_connect_timeout, ble_pair
        self._ble_client = None
        self.__ble_write_lock = Lock()
        self.__ble_send_lock = Lock()
        super().__init__(receive_queue_max, send_queue_max, log_level, name, send_timeout, retry, stats_interval, stats_history, receive_callback)
        self._request_close = False
        self._connect_thread = Thread(target=asyncio.run, args=(self.connect(),))

    def __del__(self):
        # close the current running loop
        self._request_close = True
        force_cancel_time = time() + BLE_CONNECT_THREAD_TIMEOUT
        while time() < force_cancel_time and self._connect_thread.is_alive():
            sleep(.25)
        super().__del__()

    async def connect(self):
        ''' Make the initial connection '''
        # lock out all write and send operations
        self._logger.info("Starting BLE async thread...")
        while True:
            try:
                with self.__ble_write_lock and self.__ble_send_lock:
                    self._ble_client = None
                    # find the device first with the scanner
                    if re.search(BLE_RE_MAC, self.ble_device):
                        self._logger.info(f"Attempting to connecting to BLE Address: {self.ble_device}...")
                        device = await BleakScanner.find_device_by_address(self.ble_device, timeout=self.ble_connect_timeout)
                    else:
                        self._logger.info(f"Attempting to connecting to BLE device name: {self.ble_device}...")
                        device = await BleakScanner.find_device_by_name(self.ble_device, timeout=self.ble_connect_timeout)
                    if not device:
                        self._logger.error(f"Unable to connect to device: {self.ble_device}")
                        return

                    # connect to the device
                    client = BleakClient(device, pair=self.ble_pair)
                    if self.ble_pair:
                        await client.pair()
                    if client.is_connected:
                        self._logger.info(f"Connected to device '{device.name}' ({device.address})")
                    else:
                        self._logger.error(f"Failed to connect to deivce '{device.name}' ({device.address})")

                    # verify expected services are available
                    tx_found = rx_found = False
                    for _, characteristic in client.services.characteristics.items():
                        if characteristic.uuid == BLE_GATT_CHAR_TX_UUID:
                            tx_found = True
                            #self._logger.info(f"Setting BLE Nordic UART TX MTU to {self.ble_max_write_length}...")
                            #characteristic.max_write_without_response_size = self.ble_max_write_length
                            #async with asyncio.timeout(10):
                            #    while characteristic.max_write_without_response_size == 20:
                            #        await asyncio.sleep(0.5)
                        if characteristic.uuid == BLE_GATT_CHAR_RX_UUID:
                            rx_found = True

                    if tx_found and rx_found:
                        self._logger.info(f"Connection to '{device.name}' ({device.address}) completed!")
                        self._ble_client = client

                        # Setup the receive handler when a message comes in
                        await self._ble_client.start_notify(BLE_GATT_CHAR_RX_UUID, callback=self._receive_thread)

                # Loop until disconnected or close requested
                while client.is_connected and not self._request_close:
                    await asyncio.sleep(.5)

                # if close is requested, break the look
                break

            except Exception as e:
                self._logger.error(f"Error in BLE async thread: {e.__class__.__name__}: {e}")
                if self._logger.level == 7:
                    print(traceback.format_exc())

        # close the connection if it is active
        if self._ble_client is not None and self._ble_client.is_connected:
            self._logger.info(f"Closing connection to '{device.name}' ({device.address})...")
            await self._ble_client.disconnect()
            self._logger.info(f"Connection to '{device.name}' ({device.address}) is closed.")

    def _receive_thread(self, sender: BleakGATTCharacteristic, data: bytearray):
        ''' Process an incoming message from the notify event '''
        self._logger.debug(f"Received RX notify from '{self._ble_client.name}' ({self._ble_client.address})...") # pyright: ignore[reportOptionalMemberAccess]
        self._logger.debug(f"Received data: {data}")
        try:
            if SER_START_DATA not in data or SER_END not in data:
                # if we don't have a complete message, end
                self._logger.error(f"Received data from '{self._ble_client.name}' ({self._ble_client.address}) incomplete message!") # pyright: ignore[reportOptionalMemberAccess]
                return
            received_msg = MicrocomMsg.from_bytes(data=data, source=(self._ble_client.address,))  # pyright: ignore[reportOptionalMemberAccess]
            self._logger.debug(f"Received message from '{self._ble_client.name}' ({self._ble_client.address}): {received_msg}") # pyright: ignore[reportOptionalMemberAccess]
            Thread(target=self._receive_message, args=[received_msg]).start()
        except Exception as e:
            self._logger.error(f"ERROR with Received message from '{self._ble_client.name}' ({self._ble_client.address}): {e.__class__.__name__}: {e}") # pyright: ignore[reportOptionalMemberAccess]

    def send_ack(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None):
        ''' Send ACK messages '''
        if self._ble_client is not None and self._ble_client.address == message.ip:
            with self.__ble_send_lock:
                try:
                    header, data, footer = MicrocomMsg.ack(message=message).serialize()
                    self._logger.debug(f"Sending ACK to {self._ble_client.name} ({self._ble_client.address}): {message.ip} {header}")
                    self._ble_client.write_gatt_char(BLE_GATT_CHAR_TX_UUID, header + data + footer, response=True)
                except Exception as e:
                    self._logger.error(f"Error sending ACK for {message.ip}: {header}: {e.__class__.__name__}: {e}")
        else:
            if self._ble_client is None:
                self._logger.error("Error sending ACK: No BLE server connected!")
            else:
                self._logger.error(f"Error sending ACK: message is for {message.ip}, however connected device is {self._ble_client.address}")

    def send(self, message, timeout:None|int=None, retry:None|int=None, wait_for_ack:bool=True, wait_for_reply:bool=False, data=None):
        ''' Send a message back using the tx service '''
        if self._ble_client is not None and self._ble_client.address == message.ip:
            timeout = timeout if timeout is not None else self.timeout
            retry = retry if retry is not None else self.retry
            if data is not None:
                message.data = data # if data was passed to the function, fill it in

            if message.data_length > self.ble_max_write_length:
                # if we need to fragment, break up the packet and send
                frag_count = 1
                fragment = None
                for fragment in message.frag(self.ble_max_write_length):
                    self._logger.debug(f"SEND fragment {frag_count} for pkt_id: {fragment.pkt_id} to {fragment.ip} size: {fragment.data_length}")
                    self.send(fragment, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply if message.direction != DIR_REPLY_FRAG else False)
                    if not fragment.ack_received():
                        self._logger.error(f"SEND fragment {frag_count} for pkt_id: {fragment.pkt_id} to {fragment.ip}) failed.")
                        return
                    frag_count += 1

                # check and see if the message was received
                while not self._last_sent_message.reply_received() and (time() < (self._last_sent_message.send_time + timeout) or time() < (self._last_sent_message.frag_time + timeout)): # type: ignore
                    sleep(.1)

                if self._last_sent_message.reply_received():
                    self._logger.debug(f"REPLY received for {self._last_sent_message.pkt_id}")
                else:
                    self._logger.error(f"REPLY was NOT received for {self._last_sent_message.pkt_id}")
                return

            with self.__ble_send_lock:
                try:
                    if wait_for_ack or wait_for_reply:
                        self._last_sent_message = message
                    message.send_time = time()
                    header, data, footer = message.serialize()

                    self._logger.debug(f"Sending message to {self._ble_client.address}: {message.ip} {header}")
                    self._ble_client.write_gatt_char(BLE_GATT_CHAR_TX_UUID, header + data + footer, response=True)
                except Exception as e:
                    self._logger.error(f"Error sending message: {message}: {e.__class__.__name__}: {e}")
                    return

                # if we aren't waiting for an ACK, then we don't need to retrans
                if not wait_for_ack:
                    return

                while not message.ack_received() and time() < (message.send_time + timeout): # type: ignore
                    sleep(.1)
                if message.ack_received(): # type: ignore
                    self._logger.debug(f"ACK Received for {message.pkt_id}")
                    if wait_for_ack and not wait_for_reply:
                        return
                    # if we are waiting for a reply
                    while not message.reply_received() and time() < (message.send_time + timeout): # type: ignore
                        sleep(.1)
                    self._logger.debug(f"REPLY {'received' if message.reply_received() else 'NOT received'} for {message.pkt_id}")
                    return # return regardless of if a reply was received

            # if we didn't get an ACK
            self._logger.warning(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Send timeout after {timeout} seconds waiting for ACK. Retry {message.retries} / {retry}") # type: ignore
            self._logger.debug(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Send timeout packet details: {bytes(message.data_bytes[0:100])}") # type: ignore
            if message.retries < retry: # type: ignore
                # increment the retry counter and call the send function again
                message.retries += 1 # type: ignore
                return self.send(message=message, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply)
            else:
                self._logger.error(f"ID: {message.pkt_id} to ({message.ip}:{message.port}), Type: {message.msg_type}, Retry exceeded with no ACK. Message discarded.") # type: ignore

        else:
            if self._ble_client is None:
                self._logger.error("Error sending ACK: No BLE server connected!")
            else:
                self._logger.error(f"Error sending ACK: message is for {message.ip}, however connected device is {self._ble_client.address}")
