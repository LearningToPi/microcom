''' Bluetooth LE based Client '''
from typing import Callable
from logging_handler import INFO
import logging
from time import time, sleep
from microcom.client._base import MicrocomClient, RECEIVE_QUEUE_MAX, RETRY, SEND_QUEUE_MAX, SEND_TIMEOUT, SERVER_NAME, STATS_HISTORY, STATS_INTERVAL, STATS_STREAMING, LOG_LEVEL
from microcom.msg._base import *
from microcom.exceptions import *
import re
import asyncio
import traceback
from threading import Thread, Lock
from bleak import BleakClient, BleakScanner
from bleak.backends.service import BleakGATTServiceCollection
from bleak.backends.characteristic import BleakGATTCharacteristic
from dbus_fast.aio import MessageBus
from dbus_fast import introspection as intr, BusType

from microcom.msg._base import MicrocomMsg


# Microcom BLE defaults
BLE_DEVICE_NAME = 'Microcom Server'
BLE_CONNECT_TIMEOUT = 10
BLE_PAIR_DEVICE = True
BLE_RE_MAC = r'^[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}[:_-]?[0-9a-fA-F]{2}$'
BLE_GATT_CHAR_MAX_WRITE_LENGTH = 256
BLE_GATT_CHAR_RX_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
BLE_GATT_CHAR_RX_READ_UUID = "8d970003-4a06-41c9-91a8-88836a1531cd" # Client writes ID of last message read
BLE_GATT_CHAR_TX_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
BLE_GATT_CHAR_TX_READ_UUID = "8d970002-4a06-41c9-91a8-88836a1531cd" # Server writes ID of last message read
BLE_CONNECT_THREAD_TIMEOUT = 3
BLE_SEND_CHECK_INTERVAL = .1
BLE_BACKOFF_INTERVALS = [10, 30, 60, 300]

# DBus variables
BLUEZ_SERVICE_NAME = "org.bluez"
BLUEZ_DEVICE_IFACE = "org.bluez.Device1"
BLUEZ_ADAPTER_IFACE = "org.bluez.Adapter1"
BLUEZ_OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"


class MicrocomClientBLE(MicrocomClient):
    ''' Microcom BLE client '''
    def __init__(self, ble_device:str=BLE_DEVICE_NAME, ble_max_write_length:int=BLE_GATT_CHAR_MAX_WRITE_LENGTH, ble_connect_timeout:int=BLE_CONNECT_TIMEOUT,
                 ble_pair:bool=BLE_PAIR_DEVICE,
                 receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        self.ble_device, self.ble_max_write_length, self.ble_connect_timeout, self.ble_pair = ble_device, ble_max_write_length, ble_connect_timeout, ble_pair
        self._ble_client_name, self._ble_client_addr = None, None
        self._ble_send_queue = None
        # DBus holders
        self._dbus, self._dbus_proxy, self._dbus_obj_manager = None, None, None
        self.__ble_send_lock = Lock()
        super().__init__(receive_queue_max, send_queue_max, log_level, name, send_timeout, retry, stats_interval, stats_history, receive_callback)
        self._request_close = False
        self._async_thread = Thread(target=asyncio.run, args=(self.__async_thread(),))
        self._async_thread.start()

    async def __dbus_connect(self):
        ''' Connect to DBus and store the objects for later use '''
        try:
            self._logger.info("Connecting to Bluez via DBus...")
            self._dbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._dbus_proxy = self._dbus.get_proxy_object(BLUEZ_SERVICE_NAME, "/", await self._dbus.introspect(BLUEZ_SERVICE_NAME, "/"))
            self._dbus_obj_manager = self._dbus_proxy.get_interface(BLUEZ_OBJECT_MANAGER_IFACE)
        except Exception as e:
            self._logger.error(f"Failed to connect to DBus! {e.__class__.__name__}: {e}")
            raise MicrocomDBusError(f"Error connecting to DBus: {e.__class__.__name__}: {e}") from e

    async def __dbus_get_connected_devices(self) -> list:
        ''' Return a list of devices connected via DBus '''
        try:
            # if DBus us not connected, make the connection
            if self._dbus is None or self._dbus_proxy is None or self._dbus_obj_manager is None:
                await self.__dbus_connect()

            # Call GetManagedObjects to retrieve all objects
            managed_objects = await self._dbus_obj_manager.call_get_managed_objects()

            connected_devices = []
            for path, interfaces in managed_objects.items():
                if BLUEZ_DEVICE_IFACE in interfaces:
                    device_properties = interfaces[BLUEZ_DEVICE_IFACE]
                    name = device_properties.get("Name", "Unknown Name")
                    address = device_properties.get("Address", "Unknown Address")
                    adapter = device_properties.get("Adapter", "Unknown Adapter")
                    connected = device_properties.get("Connected", "unknown")
                    connected_devices.append({"Name": name.value if not isinstance(name, str) else name, 
                                              "Address": address.value, 
                                              "Path": path, 
                                              "Connected": connected.value,
                                              "Adapter": adapter.value})
            return connected_devices
        except Exception as e:
            self._logger.error(f"Error getting connected devices from DBus: {e.__class__.__name__}: {e}")
            raise MicrocomDBusError(f"Error getting connected devices from DBus: {e.__class__.__name__}: {e}") from e

    async def __dbus_device_connected(self, device:str) -> bool:
        ''' Check if a device is already connected '''
        try:
            connected_devices = await self.__dbus_get_connected_devices()
            # determine if searching for a MAC or a name
            mac_search = re.search(BLE_RE_MAC, device) is not None
            for connected_device in connected_devices:
                if mac_search:
                    if device.upper().replace(':', '').replace('_', '') == connected_device['Address'].upper().replace(':', '').replace('_', ''):
                        return connected_device['Connected']
                else:
                    if device == connected_device['Name']:
                        return connected_device['Connected']
            return False

        except Exception as e:
            self._logger.error(f"Error checking if '{device}' is connected via DBus: {e.__class__.__name__}: {e}")
            raise MicrocomDBusError(f"Error checking if '{device}' is connected via DBus: {e.__class__.__name__}: {e}") from e

    async def __dbus_disconnect_device(self, device:str):
        ''' Attempt to disconnect a device that is currently connected using DBus '''
        try:
            connected_devices = await self.__dbus_get_connected_devices()

            # determine if searching for a MAC or a name
            mac_search = re.search(BLE_RE_MAC, device) is not None
            device_info = None
            for connected_device in connected_devices:
                if mac_search:
                    if device.upper().replace(':', '').replace('_', '') == connected_device['Address'].upper().replace(':', '').replace('_', ''):
                        if connected_device['Connected']:
                            device_info = connected_device
                            break
                else:
                    if device == connected_device['Name']:
                        if connected_device['Connected']:
                            device_info = connected_device
                            break
            
            # disconnect the device if it is connected
            if device_info is not None:
                self._logger.info(f"Attempting to disconnect previously connected device {device_info['Name']} ({device_info['Address']})...")
                device_proxy = self._dbus.get_proxy_object(BLUEZ_SERVICE_NAME, device_info['Path'], await self._dbus.introspect(BLUEZ_SERVICE_NAME, device_info['Path']))
                device_interface = device_proxy.get_interface(BLUEZ_DEVICE_IFACE)
                await device_interface.call_disconnect()

                # remove the device pairing information
                adapter_proxy = self._dbus.get_proxy_object(BLUEZ_SERVICE_NAME, device_info['Adapter'], await self._dbus.introspect(BLUEZ_SERVICE_NAME, device_info['Adapter']))
                adapter_interface = adapter_proxy.get_interface(BLUEZ_ADAPTER_IFACE)
                await adapter_interface.call_remove_device(device_info['Path'])
                self._logger.error(f"Previously connected device {device_info['Name']} ({device_info['Address']}) disconnected (and removed from Bluez).")

        except Exception as e:
            self._logger.info(f"Failed to disconnect '{device}'. {e.__class__.__name__}: {e}")

    async def __async_thread(self):
        ''' Asyncio function to run in a thread '''
        back_off_count = 0 # used to increment the backoff interval

        while True:
            try:
                # Check if device is already connected
                if await self.__dbus_device_connected(self.ble_device):
                    self._logger.info(f"Device {self.ble_device} is already connected to bluetooth outside of this app.")
                    await self.__dbus_disconnect_device(self.ble_device)

                self._ble_client_name, self._ble_client_addr = None, None

                # find the device first with the scanner
                if re.search(BLE_RE_MAC, self.ble_device):
                    self._logger.info(f"Attempting to connecting to BLE Address: {self.ble_device}...")
                    device = await BleakScanner.find_device_by_address(self.ble_device, timeout=self.ble_connect_timeout)
                else:
                    self._logger.info(f"Attempting to connecting to BLE device name: {self.ble_device}...")
                    device = await BleakScanner.find_device_by_name(self.ble_device, timeout=self.ble_connect_timeout)
                if not device:
                    self._logger.error(f"Unable to connect to BLE device: {self.ble_device}: Device not found")
                    raise MicrocomConnectFailed(f"Unable to connect to BLE device: {self.ble_device}: Device not found")

                # connect to the device
                async with BleakClient(device) as cl:
                    self._logger.info(f"Connected to device '{device.name}' ({device.address})")
                    if self.ble_pair:
                        await cl.pair()
                    self._ble_client_name, self._ble_client_addr = cl.name, cl.address # save the client name and address for reference elsewhere
                    back_off_count = 0 # reset the backoff count if we have a successful connection

                    # verify expected services are available
                    tx_found = rx_found = False
                    for _, characteristic in cl.services.characteristics.items():
                        if characteristic.uuid == BLE_GATT_CHAR_TX_UUID:
                            tx_found = True
                        if characteristic.uuid == BLE_GATT_CHAR_RX_UUID:
                            rx_found = True

                    if tx_found and rx_found:
                        self._logger.info(f"Connection to '{self._ble_client_name}' ({self._ble_client_addr}) completed!")

                        # Setup the receive handler when a message comes in
                        await cl.start_notify(BLE_GATT_CHAR_RX_UUID, callback=self._receive_thread)
                        await cl.start_notify(BLE_GATT_CHAR_TX_READ_UUID, callback=self._receive_ack_thread)

                    while not self._request_close:
                        # check for pending message to send
                        if isinstance(self._ble_send_queue, dict):
                            self._logger.debug(f"Bluetooth Nordic UART Transmit: {self._ble_send_queue}")
                            await cl.write_gatt_char(**self._ble_send_queue)
                            # clear the message to notify the send is complete
                            self._ble_send_queue = None
                        await asyncio.sleep(BLE_SEND_CHECK_INTERVAL)

                    self._logger.info(f"Closing connection to '{self._ble_client_name}' ({self._ble_client_addr})...")
                    return

            except Exception as e:
                backoff_time = BLE_BACKOFF_INTERVALS[back_off_count] if back_off_count < len(BLE_BACKOFF_INTERVALS) else BLE_BACKOFF_INTERVALS[-1]
                self._logger.error(f"Error in Async main thread: {e.__class__.__name__}: {e}.  Backoff for {backoff_time} seconds (backoff count {back_off_count})")
                back_off_count += 1 # increment for the next loop
                await asyncio.sleep(backoff_time)
                traceback.print_exc()

    def __del__(self):
        self.close()

    def close(self):
        # close the current running loop
        self._logger.info(f"Terminating BLE Client...")
        self._request_close = True
        force_cancel_time = time() + BLE_CONNECT_THREAD_TIMEOUT
        while time() < force_cancel_time and self._async_thread is not None and self._async_thread.is_alive():
            sleep(.25)

        # close DBus
        if self._dbus and self._dbus.connected:
            self._logger.info("Closing DBus connection...")
            self._dbus.disconnect()

        super().close()

    def is_connected(self) -> bool:
        ''' Return True if BLE is conencted to the server '''
        return self._ble_client_name is not None

    def _receive_thread(self, sender: BleakGATTCharacteristic, data: bytearray):
        ''' Process an incoming message from the notify event '''
        self._logger.debug(f"Received RX notify from '{self._ble_client_name}' ({self._ble_client_addr})...") # pyright: ignore[reportOptionalMemberAccess]
        self._logger.debug(f"Received data: {data}")
        try:
            if SER_START_DATA not in data or SER_END not in data:
                # if we don't have a complete message, end
                self._logger.error(f"Received data from '{self._ble_client_name}' ({self._ble_client_addr}) incomplete message!") # pyright: ignore[reportOptionalMemberAccess]
                return
            received_msg = MicrocomMsg.from_bytes(data=data, source=(self._ble_client_addr,))  # pyright: ignore[reportOptionalMemberAccess]
            # send back an ACK
            with self.__ble_send_lock:
                self._logger.debug(f"Sending ACK for message ID: {received_msg.pkt_id}")
                self._ble_send_queue = {'char_specifier': BLE_GATT_CHAR_RX_READ_UUID, 'data': str(received_msg.pkt_id).encode('utf-8'), 'response': True}
            self._logger.debug(f"Received message from '{self._ble_client_name}' ({self._ble_client_addr}): {received_msg}") # pyright: ignore[reportOptionalMemberAccess]
            Thread(target=self._receive_message, args=[received_msg]).start()
        except Exception as e:
            self._logger.error(f"ERROR with Received message from '{self._ble_client_name}' ({self._ble_client_addr}): {e.__class__.__name__}: {e}") # pyright: ignore[reportOptionalMemberAccess]

    def _receive_ack_thread(self, sender:BleakGATTCharacteristic, data: bytearray):
        ''' Process an incoming ACL message '''
        self._logger.debug(f"Received ACK notify from '{self._ble_client_name}' ({self._ble_client_addr})...") # pyright: ignore[reportOptionalMemberAccess]
        self._logger.debug(f"Received ACK data: {data}")
        try:
            if self._last_sent_message.pkt_id == int(data.decode('utf-8')):
                self._last_sent_message.ack_time = time()
                self._logger.debug(f"Received ACK for message ID: {int(data)}")
            else:
                self._logger.warning(f"Received ACK for message ID: {int(data)}, does not match last sent message ID {self._last_sent_message.pkt_id if self._last_sent_message is not None else None}")
        except Exception as e:
            self._logger.error(f"ERROR with Received message from '{self._ble_client_name}' ({self._ble_client_addr}): {e.__class__.__name__}: {e}") # pyright: ignore[reportOptionalMemberAccess]

    def send_ack(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None):
        ''' Send ACK messages - ACK is handled during receive with bluetooth LE '''
        pass

    def send(self, message:MicrocomMsg, wait_for_ack:bool=True, wait_for_reply:bool=False, timeout:None|int=None, retry:None|int=None, data:str|bytes|None=None):
        ''' Send a message back using the tx service '''
        if self.is_connected():
            timeout = timeout if timeout is not None else self.timeout
            retry = retry if retry is not None else self.retry
            if data is not None:
                message.data = data # if data was passed to the function, fill it in

            if message.total_length > self.ble_max_write_length:
                # if we need to fragment, break up the packet and send
                frag_count = 1
                fragment = None
                for fragment in message.frag(self.ble_max_write_length - (message.total_length - message.data_length)): # frag size is max write minus header size
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

            try:
                if wait_for_ack or wait_for_reply:
                    self._last_sent_message = message
                message.send_time = time()

                # check to make sure the send queue is empty
                stop_time = time() + timeout
                while self._ble_send_queue is not None and time() < stop_time:
                    sleep(.05)

                # set the write into the buffer so it can be sent by the asyncio thread
                with self.__ble_send_lock:
                    header, data, footer = message.serialize()
                    self._logger.debug(f"Sending message to {self._ble_client_name} ({self._ble_client_addr}): {message}")
                    self._ble_send_queue = {'char_specifier': BLE_GATT_CHAR_TX_UUID, 'data': header + data + footer, 'response': True}
                    # wait until the send is processed
                    stop_time = time() + timeout
                    while self._ble_send_queue is not None and time() < stop_time:
                        sleep(.05)

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
            self._logger.error("Error sending ACK: No BLE server connected!")



if __name__ == '__main__':
    client = MicrocomClientBLE('Microcom BLE Test', log_level='debug')
    print('.')