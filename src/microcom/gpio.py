
from typing import Callable
from logging_handler import create_logger, INFO
from microcom.msg._base import MicrocomMsg, MSG_TYPE_PIN_READ, MSG_TYPE_PIN_SET
from microcom.exceptions import MicrocomException

REPR_RE_NAME = r'Pin\((GPIO[0-9]+)[,\)]'
REPR_RE_MODE = r'mode=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_PULL = r'pull=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_ALT = r'alt=([a-zA-Z0-9_]*)[,\)]'

IN = 'in'
OUT = 'out'
OPEN_DRAIN = 'open_drain'
VALID_PIN_MODE = (IN, OUT, OPEN_DRAIN)

PULL_NONE = None
PULL_UP = 'pull_up'
PULL_DOWN = 'pull_down'
VALID_PIN_PULL = (PULL_NONE, PULL_UP, PULL_DOWN)


class GeneralGPIO:
    ''' Class to represent a generic GPIO device '''
    def __init__(self, pin:int, log_level:str=INFO, irq_register:Callable|None=None):
        self.pin = pin
        self._irq_handler = None
        self._irq_register = irq_register
        self._logger = create_logger(log_level, name=f"GPIO{pin}")
        self._set_mode = IN
        self._set_pull = None

    def read(self) -> dict:
        ''' Read the current state of the GPIO pin.  Return data:
            {'mode': [mode_str], 'pull': [pull_str], 'value': [0/1], 'name': [pin_name] }'''
        raise NotImplementedError("READ() function should be replaced by inheritting class")

    def set(self, **kwargs):
        ''' Set a property or value for the GPIO pin '''
        raise NotImplementedError("SET() function should be replaced by inheritting class")

    @property
    def mode(self) -> int:
        ''' Return the current mode (IN, OUT, OPEN_DRAIN) '''
        return self.read()['mode']

    @mode.setter
    def mode(self, value):
        ''' Set the mode '''
        self.set(mode=value)

    @property
    def value(self) -> int:
        ''' return the current value '''
        return self.read()['value']

    @value.setter
    def value(self, new_value):
        self.set(value=new_value)

    @property
    def pull(self) -> int:
        ''' Get the current pull (none, up, down, ...) '''
        return self.read()['pull']

    @pull.setter
    def pull(self, value):
        ''' Set the pull '''
        self.set(pull=value)

    def set_high(self):
        ''' Set the value to high (1) '''
        self.set(value=1)

    def set_low(self):
        ''' Set the value to low (0) '''
        self.set(value=0)

    # aliases
    set_1 = set_high
    set_on = set_high
    set_0 = set_low
    set_off = set_low

    @property
    def mode_in(self):
        ''' Return TRUE if pin is set for input '''
        return self.read()['mode'] == IN

    @property
    def mode_out(self):
        ''' Return TRUE if pin is set for output '''
        return self.read()['mode'] == OUT

    @property
    def pull_none(self):
        ''' Return TRUE if the pull up/down is disabled '''
        return self.read()['pull'] is None

    @property
    def pull_up(self):
        ''' Return TRUE if the pull up/down is disabled '''
        return self.read()['pull'] == PULL_UP

    @property
    def pull_down(self):
        ''' Return TRUE if the pull up/down is disabled '''
        return self.read()['pull'] == PULL_DOWN

    def set_irq(self, irq_handler:Callable, rising:bool=True, falling:bool=True, message:bool=True, execute:dict|list|None=None):
        ''' Configure an IRQ on the GPIO ping for rising or falling edge '''
        self._irq_handler = irq_handler
        if not isinstance(self._irq_register, Callable):
            raise NotImplementedError('No IRQ Handler function registered. Unable to register a handler.')
        self._irq_register(self.pin, irq_handler)
        self.set(irq={'rising': rising, 'falling': falling, 'message': message, 'execute': execute})

    def __repr__(self):
        data = self.read()
        return f"{self.__class__.__name__}(mode={data['mode'].upper()}, value={data['value']}, pull={data['pull'].upper()})"


class MicrocomGPIO(GeneralGPIO):
    ''' Class to handle general GPIO pins '''
    def __init__(self, pin:int, send:Callable, message_class:MicrocomMsg, log_level:str=INFO, mode=IN, pull=PULL_NONE, irq_register:Callable|None=None, value:int|None=None):
        self._message_class, self._send = message_class, send
        super().__init__(pin=pin, log_level=log_level, irq_register=irq_register)
        self._set_mode, self._set_pull = mode, pull
        self.set(mode=mode, pull=pull, value=value)

    def read(self) -> dict:
        ''' read the pin configuration and status '''
        message = self._message_class(msg_type=MSG_TYPE_PIN_READ, data=[self.pin]) # type: ignore
        self._logger.debug(f"READING: GPIO: {self.pin}...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, self._message_class) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error getting GPIO {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, dict):
            raise ValueError(f"Expected dict but received: {message.reply_msg.data}")
        self._logger.debug(f"READING: GPIO: {self.pin}, {message.reply_msg.data}")
        # ESP32 can't return the mode, pull, name, so return our saved values instead
        data = message.reply_msg.data[str(self.pin)]
        if data['pin'] is None:
            data['pin'] = self.pin
            data['mode'] = self._set_mode
            data['pull'] = self._set_pull
        return data

    def set(self, **kwargs):
        ''' Write the ping configuration '''
        # remove value if it is None
        if 'value' in kwargs and kwargs['value'] is None:
            kwargs.pop('value')
        if 'pull' in kwargs and kwargs['pull'] not in VALID_PIN_PULL:
            raise ValueError(f"Value '{kwargs['pull']}' not in supported pull values: {VALID_PIN_PULL}")
        if 'value' in kwargs and kwargs['value'] not in [0, 1, True, False]:
            raise ValueError(f"Value '{kwargs['value']}' not in supported values: {[0, 1, True, False]}")
        if 'mode' in kwargs and kwargs['mode'] not in VALID_PIN_MODE:
            raise ValueError(f"Value '{kwargs['mode']}' not in supported pin modes: {VALID_PIN_MODE}")
        if 'mode' not in kwargs:
            kwargs['mode'] = self._set_mode
        if 'pull' not in kwargs:
            kwargs['pull'] = self._set_pull
        message = self._message_class(msg_type=MSG_TYPE_PIN_SET, data={str(self.pin): kwargs}) # type: ignore
        self._logger.debug(f"WRITING: GPIO: {self.pin}, {kwargs}...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, self._message_class) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error setting GPIO {self.pin}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'},"
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        self._logger.debug(f"WRITING: GPIO: {self.pin}, {kwargs} COMPLETE, Return code: {message.reply_msg.return_code}")
