
from logging_handler import create_logger, INFO
from typing import Callable
from time import time
from microcom.msg._base import MicrocomMsg, MSG_TYPE_PWM_READ, MSG_TYPE_PWM_SET
from microcom.exceptions import *

REPR_RE_NAME = r'Pin\((GPIO[0-9]+)[,\)]'
REPR_RE_MODE = r'mode=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_PULL = r'pull=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_ALT = r'alt=([a-zA-Z0-9_]*)[,\)]'


class MicrocomPWM:
    ''' Class to handle PWM pins
        NOTE: Invert is handled on the client since not all Microcom ports support this.  If invert = True, the duty_16 is subtracted from 65535 before sending '''
    def __init__(self, pin:int, send:Callable, message_class:MicrocomMsg, log_level:str=INFO, freq:int=10_000, duty_u16:int=0, invert:bool=False):
        self.pin, self._send, self._message_class, self._invert = pin, send, message_class, invert
        self._logger = create_logger(log_level, name=f"PWM{pin}")
        self._write(freq=freq, duty_u16=duty_u16, invert=False)

    def get_all(self) -> dict:
        ''' read the pin configuration and status '''
        message = self._message_class(msg_type=MSG_TYPE_PWM_READ, data=[self.pin]) # type: ignore
        self._logger.debug(f"READING: PWM: {self.pin}...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, self._message_class) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error getting PWM {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, dict):
            raise ValueError(f"Expected dict but received: {message.reply_msg.data}")
        self._logger.debug(f"READING: PWM: {self.pin}, {message.reply_msg.data}")
        return message.reply_msg.data[str(self.pin)]

    def _write(self, **kwargs):
        ''' Write the pin configuration '''
        # invert the duty_u16 if invert set
        if self._invert and 'duty_u16' in kwargs:
            kwargs['duty_u16'] = 65535 - kwargs['duty_u16']
        message = self._message_class(msg_type=MSG_TYPE_PWM_SET, data={str(self.pin): kwargs}) # type: ignore
        self._logger.debug(f"WRITING: PWM: {self.pin}, {kwargs}...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, self._message_class) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error setting PWM {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        self._logger.debug(f"WRITING: PWM: {self.pin}, {kwargs} COMPLETE, Return code: {message.reply_msg.return_code}")

    @property
    def freq(self) -> int:
        ''' Return the current mode (IN, OUT, OPEN_DRAIN) '''
        return self.get_all()['freq']

    @freq.setter
    def freq(self, value:int):
        ''' Set the mode '''
        self._write(freq=value)

    @property
    def duty_u16(self) -> int:
        ''' return the current value '''
        return self.get_all()['duty_u16']

    @duty_u16.setter
    def duty_u16(self, new_value:int):
        self._write(duty_u16=new_value)

    @property
    def duty_percent(self) -> float:
        ''' Get the current pull (none, up, down, ...) '''
        u16 = self.get_all()['duty_u16']
        u16 = 65535 - u16 if self._invert else u16
        return round(u16 / 65535, 2)

    @duty_percent.setter
    def duty_percent(self, value:float):
        ''' Set the pull '''
        self._write(duty_u16=int(round(65535 * (value/ 100.0), 0)))

    @property
    def invert(self) -> bool:
        ''' Return TRUE if invert is set '''
        return self._invert

    @invert.setter
    def invert(self, value:bool):
        self._invert = value

    def __repr__(self):
        data = self.get_all()
        return f"{self.__class__.__name__}(pin={self.pin}, freq={data['freq']}, duty_u16={data['duty_u16']}, duty_ns=NotImplemented, invert={self._invert})"
