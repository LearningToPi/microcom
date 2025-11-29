
from logging_handler import create_logger, INFO
from typing import Callable
from time import time
from microcom.msg._base import MicrocomMsg, MSG_TYPE_PLATFORM_SPEC, MSG_TYPE_MONITOR_UPDATE
from microcom.exceptions import MicrocomException, MicrocomPIOException


class MicrocomRP2PwmIn:
    ''' Class to represent a RP PIO input sensor to read PWM signals '''
    def __init__(self, pin:int, send:Callable, log_level:str=INFO, freq:int=10_000, sm:int|None=None, active:bool=True, invert:bool=False, monitor:bool=True):
        self.pin, self._send, self._invert, self._sm = pin, send, invert, sm
        self._logger = create_logger(log_level, name=f"RP2PWM-In{pin}")
        self._configure(pin=pin, freq=freq, active=active, sm=sm, function='pio_read_pwm_setup', log_level=log_level)
        if monitor:
            self.monitor_add()

    def __del__(self):
        self.monitor_remove()

    def _configure(self, **kwargs):
        ''' Push the configuration to the server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_PLATFORM_SPEC, data=kwargs)
        self._logger.info(f"Configuring: {message.data}")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error setting PWM {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        self._logger.debug(f"Configuring {message.data} COMPLETE, Assigned SM: {message.reply_msg.data}, Return code: {message.reply_msg.return_code}")
        self._sm = message.reply_msg.data

    def delete(self, function:str='pio_read_pwm_delete'):
        ''' Shutdown and remove the RP2 PWM PIO input sensor '''
        message = MicrocomMsg(msg_type=MSG_TYPE_PLATFORM_SPEC, data={'function': function, 'sm': self._sm})
        self._logger.info(f"Deleting StateMachine: {message.data}")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error deleting StateMachine {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if message.reply_msg.data:
            self._logger.debug(f"Deleting StateMachine successful, Return code: {message.reply_msg.return_code}")
        else:
            raise MicrocomException(f"Error deleting StateMachine {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def read(self, get:str='u16', function:str='pio_read_pwm'):
        ''' Read the PWM input '''
        message = MicrocomMsg(msg_type=MSG_TYPE_PLATFORM_SPEC, data={'function': function, 'get': get, 'sm': self._sm})
        self._logger.debug(f"Reading PWM as {'percent' if get == 'percent' else 'u16'}")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error deleting StateMachine {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    def monitor_add(self, get:str='u16', function:str='pio_read_pwm'):
        ''' Create a monitor on the server to return '''
        message = MicrocomMsg(msg_type=MSG_TYPE_MONITOR_UPDATE, data=[{'name': self.monitor_name, 'msg_type': MSG_TYPE_PLATFORM_SPEC, 'data': {'function': function, 'get': get, 'sm': self._sm}}])
        self._logger.debug(f"Adding monitor for {function} as {'percent' if get == 'percent' else 'u16'}...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error deleting StateMachine {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    def monitor_remove(self):
        ''' Remove a monitor on the server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_MONITOR_UPDATE, data=[{'name': self.monitor_name, 'remove': True}])
        self._logger.debug("removing monitor...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error deleting StateMachine {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    @property
    def monitor_name(self) -> str:
        ''' Return the monitor name created on the server '''
        return f"{self.__class__.__name__}.{self.pin}"

class MicrocomRP2TachIn(MicrocomRP2PwmIn):
    ''' Class to represent a tach sensor based on a PWM input '''
    def __init__(self, pin:int, send:Callable, pulse_per_rev:int, log_level:str=INFO, freq:int=10000, sm:int|None=None, active:bool=True, invert:bool=False, monitor:bool=True): # pylint: disable=W0231
        self.pin, self._send, self._invert, self._sm, self._pulse_per_rev = pin, send, invert, sm, pulse_per_rev
        self._logger = create_logger(log_level, name=f"RP2PWM-In{pin}")
        self._configure(pin=pin, freq=freq, active=active, pulse_per_rev=pulse_per_rev, sm=sm, function='pio_read_tach_setup', log_level=log_level)
        if monitor:
            self.monitor_add()

    def __del__(self):
        self.monitor_remove()

    def delete(self, function="pio_read_tach_delete"):
        ''' Delete the PIO instance '''
        return super().delete(function)

    def read(self, get='rpm', function='pio_read_tach'):
        ''' Read the tach in RPM '''
        return super().read(get='rpm', function=function)

    def monitor_add(self, get: str = 'rpm', function: str = 'pio_read_tach'):
        return super().monitor_add(get, function)
