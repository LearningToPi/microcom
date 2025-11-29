
from logging_handler import create_logger, INFO
from typing import Callable
from time import time
from microcom.msg._base import MicrocomMsg, MSG_TYPE_BUS_CMD
from microcom.exceptions import *
from microcom.bmx280 import Bmx280I2C
from microcom.ads111x import Ads111xI2C
from microcom.pfc8575 import Pfc8575I2C
from microcom.ina3221 import Ina3221I2C, ShuntConfig


class MicrocomI2C:
    ''' Generic class to act as the underlay for other bus protocols (I2C, SPI, UART, etc) 
        Class is generated from the MicrocomClient class. '''
    def __init__(self, bus_id:int, send:Callable, message_class:MicrocomMsg, log_level:str=INFO, irq_register:Callable|None=None):
        # NOTE: Need to get the MicrocomMsg pointer as a variable, otherwise isinstance checks don't match for some reason.  MicrocomMsg != microcom.msg._base.MicrocomMsg
        self._bus_id, self._send, self._message_class, self._irq_register = bus_id, send, message_class, irq_register
        self._logger = create_logger(log_level, name=f"I2C{bus_id}")

    def scan(self):
        ''' run a scan on the I2C bus and return the attached devices '''
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'I2C', 'bus_id': self._bus_id, 'bus_cmd': 'scan'}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error scanning i2c bus {self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, list):
            raise ValueError(f"Expected a list but received: {message.reply_msg.data}")
        return message.reply_msg.data

    def read(self, address:int, nbytes:int=1) -> bytes:
        ''' Read a number of bytes from the device '''
        start_time = time()
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'I2C',
                                                                       'bus_id': self._bus_id,
                                                                       'bus_cmd': 'readfrom',
                                                                       'cmd_params': {'addr': address, 'nbytes': nbytes}}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error scanning i2c bus {self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, (bytes, bytearray)):
            raise ValueError(f"Expected bytes but received: {message.reply_msg.data}")
        self._logger.debug(f"READING: I2C Address: {address}, Byte count: {nbytes}, data: {message.reply_msg.data}, query time: {time() - start_time}")
        return bytes(message.reply_msg.data)

    def readmem(self, address:int, memaddress:int|list, nbytes:int=1) -> bytes:
        ''' Read a specified number of bytes from a device's memory address '''
        start_time = time()
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'I2C',
                                                               'bus_id': self._bus_id,
                                                               'bus_cmd': 'readfrom_mem',
                                                               'cmd_params': {'addr': address, 'memaddr': memaddress, 'nbytes': nbytes}}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error scanning i2c bus {self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, (bytes, bytearray)):
            raise ValueError(f"Expected bytes but received: {message.reply_msg.data}")
        self._logger.debug(f"READING: I2C Address: {address}, Mem Address: {memaddress}, Byte count: {nbytes}, data: {message.reply_msg.data}, query time: {time() - start_time}")
        return bytes(message.reply_msg.data)

    def write(self, address:int, data:bytes) -> int:
        ''' Write the data to the I2C bus with the address specified as the first byte '''
        start_time = time()
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'I2C',
                                                               'bus_id': self._bus_id,
                                                               'bus_cmd': 'writeto',
                                                               'cmd_params': {'addr': address, 'buf': data, 'stop': True}}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error scanning i2c bus {self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, int):
            raise ValueError(f"Expected int but received: {message.reply_msg.data}")
        self._logger.debug(f"WRITING: I2C Address: {address} send data {data}, reply: {message.reply_msg.data}, query time: {time() - start_time}")
        return message.reply_msg.data


    def writemem(self, address:int, memaddress:int, data:bytes, verify:bool=True, retry=2) -> bool|None:
        ''' Write specified data to the memory address of the device.  If verify, the data is read back to verify accuracy.
            If retry>0 and the data does not match, it will be retried up to X times. '''
        # after the write, verify the data
        while retry > 0:
            # write the data
            start_time = time()
            message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'I2C',
                                                                   'bus_id': self._bus_id,
                                                                   'bus_cmd': 'writeto_mem',
                                                                   'cmd_params': {'addr': address, 'memaddr': memaddress, 'buf': data}}) # type: ignore
            self._send(message, wait_for_reply=True)
            if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
                raise MicrocomException(f"Error writing to i2c bus {self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
            # no need to validate data, a write returns nothing
            self._logger.debug(f"WRITING: I2C Address: {address}, Mem Address: {memaddress}, data: {data}, query time: {time() - start_time}")
            if not verify:
                return
            while retry > 0:
                start_time = time()
                device_data = self.readmem(address=address, memaddress=memaddress, nbytes=len(data))
                if device_data == data:
                    self._logger.debug(f"WRITE_VERIFY: I2C Address: {address}, Mem Address: {memaddress}, data: {data}, verify time: {time() - start_time}")
                    return True
                retry -= 1
        raise MicrocomRetryExceeded(f"Retry exceeded while writing to I2C Bus {self._bus_id}: address: {address}, memory address: {memaddress}, data: {data}")

    def bmx280(self, address=118, log_level=INFO) -> Bmx280I2C:
        ''' initialize a bme280 / bmp280 device on the I2C bus '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Bmx280I2C(address=address, read_func=self.readmem, write_func=self.writemem, log_level=log_level)

    def ads111x(self, address=72, log_level=INFO) -> Ads111xI2C:
        ''' initialize an ADS111x device on the I2C bus '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Ads111xI2C(address=address, readmem_func=self.readmem, writemem_func=self.writemem, write_func=self.write, log_level=log_level)

    def pfc8575(self, address=32, log_level=INFO) -> Pfc8575I2C:
        ''' initialize a PFC8575 16 pin GPIO expander '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Pfc8575I2C(address=address, bus_id=self._bus_id, read_func=self.read, write_func=self.write, log_level=log_level, irq_register=self._irq_register)

    def pca9548a(self, channel:int, address=112, log_level=INFO):
        ''' initialize a PCA9548a I2C multiplexer '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Pca9548aI2C_Channel(channel=channel, address=address, bus_id=self._bus_id, send=self._send, message_class=self._message_class, log_level=log_level, irq_register=self._irq_register)

    def ina3221(self, address=64, log_level=INFO, shunt1:ShuntConfig|None=None, shunt2:ShuntConfig|None=None, shunt3:ShuntConfig|None=None) -> Ina3221I2C:
        '''' initialize a INA3221 bus sensor '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Ina3221I2C(address=address, readmem_func=self.readmem, writemem_func=self.writemem, log_level=log_level, shunt1=shunt1, shunt2=shunt2, shunt3=shunt3)

class Pca9548aI2C_Channel(MicrocomI2C):
    ''' Class to abstract an I2C channel on an I2C expander '''
    def __init__(self, channel:int, address:int, bus_id:int, send:Callable, message_class:MicrocomMsg, log_level:str=INFO, irq_register:Callable|None=None):
        super().__init__(bus_id=bus_id, send=send, message_class=message_class, log_level=log_level, irq_register=irq_register)
        self.channel, self._address = channel, address

    def _set_channel(self):
        ''' Set the i2c multiplexer to the proper channel '''
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data=self._channel_cmd()) # type: ignore
        self._send(message, wait_for_reply=True)

    def _channel_cmd(self) -> dict:
        ''' Return the bus command to set the multiplexer channel '''
        return {'bus_type': 'I2C',
                'bus_id': self._bus_id,
                'bus_cmd': 'writeto',
                'cmd_params': {'addr': self._address, 'buf': (1 << self.channel).to_bytes(1, 'big'), 'stop': True}}

    def scan(self):
        self._set_channel()
        return super().scan()

    def read(self, address: int, nbytes: int = 1) -> bytes:
        self._set_channel()
        return super().read(address, nbytes)

    def write(self, address: int, data: bytes) -> int:
        self._set_channel()
        return super().write(address, data)

    def readmem(self, address: int, memaddress: int | list, nbytes: int = 1) -> bytes:
        self._set_channel()
        return super().readmem(address, memaddress, nbytes)

    def writemem(self, address: int, memaddress: int, data: bytes, verify: bool = True, retry=2) -> bool | None:
        self._set_channel()
        return super().writemem(address, memaddress, data, verify, retry)

    def pfc8575(self, address=32, log_level=INFO) -> Pfc8575I2C:
        ''' Return a Pfc8575 object with the proper bus command added for IRQ handling '''
        if address not in self.scan():
            raise MicrocomBusDeviceNotFound(f"Address {address} not present on I2C bus {self._bus_id}")
        return Pfc8575I2C(address=address, bus_id=self._bus_id, read_func=self.read, write_func=self.write, log_level=log_level, irq_register=self._irq_register)
