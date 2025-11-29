'''
Class to represent a GPIO Pin on either the client or the server
'''
import re

#REPR_RE = r'Pin\((GPIO[0-9]+),\s*mode=([a-zA-Z0-9_]+),\s*pull=([a-zA-Z0-9_]+),\s*alt=([a-zA-Z0-9_]*)\)'
#REPR_RE_TEXT = ('name', 'mode', 'pull', 'alt')
REPR_RE_NAME = r'Pin\((GPIO[0-9]+)[,\)]'
REPR_RE_MODE = r'mode=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_PULL = r'pull=([a-zA-Z0-9_]+)[,\)]'
REPR_RE_ALT = r'alt=([a-zA-Z0-9_]*)[,\)]'

IN = 0
OUT = 1
OPEN_DRAIN = 2
ALT = 3
PWM = 'PWM'
I2C = 'I2C'
VALID_PIN_MODE = (IN, OUT, OPEN_DRAIN, ALT)
PIN_MODE_TEXT = ('IN', 'OUT', 'OPEN_DRAIN', 'ALT')

PULL_NONE = None
PULL_UP = 1
PULL_DOWN = 2
VALID_PIN_PULL = (PULL_NONE, PULL_UP, PULL_DOWN)
PIN_PULL_TEXT = (None, 'PULL_UP', 'PULL_DOWN')

VALID_PIN_ALT = (PWM, I2C)
PIN_ALT_TEXT = (None, 'SPI', 'UART', 'I2C', 'PWM', 'SIO', 'PIO0', 'PIO1', 'GPCK', 'USB', '31')


class MicrocomPin:
    ''' Class to represent a GPIO Pin on the Microcom Server '''
    REQ_FIELDS = ('pin', 'value', 'mode', 'pull')

    def __init__(self):
        self._data = {}

    def _raise_data_error(self):
        ''' Raises an error if all required fields are not present or valid '''
        for field in self.REQ_FIELDS:
            if field not in self._data:
                raise ValueError(f"MicrocomPin object missing field: {field}")
        if not isinstance(self._data['pin'], int):
            raise ValueError(f"MicrocomPin pin field must be an int, has: {self._data['pin']}")
        if not isinstance(self._data['value'], int):
            raise ValueError(f"MicrocomPin pin value must be an int (0 or 1), has: {self._data['value']}")
        if self._data['mode'] not in VALID_PIN_MODE:
            raise ValueError(f"MicrocomPin mode field must be an int, has: {self._data['mode']}")
        if self._data['pull'] not in VALID_PIN_PULL:
            raise ValueError(f"MicrocomPin pull field must be an int, has: {self._data['pull']}")

    @classmethod
    def load_pin(cls, pin:int, pin_repr:str, pin_value:int):
        ''' Load a pin on the server using the repr(Pin(x)) output '''
        pin_obj = cls()
        name_re = re.search(REPR_RE_NAME, repr(pin_repr))
        mode_re = re.search(REPR_RE_MODE, repr(pin_repr))
        pull_re = re.search(REPR_RE_PULL, repr(pin_repr))
        alt_re = re.search(REPR_RE_ALT, repr(pin_repr))
        if name_re and mode_re:
            try:
                pin_obj._data = {
                    'pin': pin,
                    'name': name_re.groups()[0],
                    'mode': PIN_MODE_TEXT.index(mode_re.groups()[0]),
                    'pull': PIN_PULL_TEXT.index(pull_re.groups()[0]) if pull_re else PULL_NONE,
                    'alt': alt_re.groups()[0] if alt_re else 'n/a',
                    'value': pin_value
                }
            except ValueError as e:
                raise ValueError(f"MicrocomPin index error loading pin: {e}") from e
        pin_obj._raise_data_error()
        return pin_obj

    @classmethod
    def load_dict(cls, data:dict):
        ''' Load pin data from a dict '''
        pin_obj = cls()
        pin_obj._data = data
        pin_obj._raise_data_error()
        return pin_obj

    @property
    def alt(self):
        ''' Return the alt mode of the pin '''
        return self._data['alt']

    @property
    def pin(self) -> int:
        ''' Return the pin number '''
        return self._data['pin']

    @property
    def name(self):
        ''' Return the Micropython Pin Name '''
        return self._data.get('name', None)

    def update(self, value:int|None=None, mode:int|None=None, pull:int|None=None):
        ''' Update the provided values as one '''
        self._data['value'] = value if value is not None else self._data['value']
        self._data['mode'] = mode if mode is not None else self._data['mode']
        self._data['pull'] = pull if pull is not None else self._data['pull']
        self._raise_data_error()
        self._set_pin()

    @property
    def value(self) -> int:
        ''' Return the value of the pin '''
        return self._data['value']
    
    @value.setter
    def value(self, new_value):
        if new_value not in (0, 1, True, False):
            raise ValueError(f"Value must be a bool, 0 or 1, got {new_value}")
        self._data['value'] = int(new_value)
        self._raise_data_error()
        self._set_pin()

    @property
    def mode(self):
        ''' Get the current mode of the device '''
        return self._data.get('mode', None) if self._data.get('mode', None) != 'ALT' else self._data.get('alt')

    @mode.setter
    def mode(self, value):
        if value in VALID_PIN_MODE:
            self._data['mode'] = value
        else:
            raise ValueError(f"Value {value} not a valid mode")
        self._raise_data_error()
        self._set_pin()

    @property
    def pull(self):
        ''' Return the current pull setting for the pin '''
        return self._data.get('pull', None)

    @pull.setter
    def pull(self, value):
        if value in VALID_PIN_PULL:
            self._data['pull'] = value
        else:
            raise ValueError(f"Value {value} not a valid pull")
        self._raise_data_error()
        self._set_pin()

    @property
    def irq_rising(self):
        ''' Return TRUE if IRQ is set for rising edge '''
        return PIN_PULL_TEXT.index(self._data.get('pull')) == IRQ_RISING

    @irq_rising.setter
    def irq_rising(self, value):
        if isinstance(value, bool):
            self._data['pull'] = IRQ_RISING
        else:
            raise ValueError(f"Value {value} not a bool")
        self._raise_data_error()
        self._set_pin()

    @property
    def irq_falling(self):
        ''' Return TRUE if IEQ is set for falling edge '''
        return PIN_PULL_TEXT.index(self._data.get('pull')) == IRQ_FALLING

    @irq_falling.setter
    def irq_falling(self, value):
        if isinstance(value, bool):
            self._data['pull'] = IRQ_FALLING
        else:
            raise ValueError(f"Value {value} not a bool")
        self._raise_data_error()
        self._set_pin()

    @property
    def pull_up(self):
        ''' Return TRUE if pin has pull-up set '''
        return PIN_PULL_TEXT.index(self._data.get('pull')) == PULL_UP

    @pull_up.setter
    def pull_up(self, value):
        if isinstance(value, bool):
            self._data['pull'] = PULL_UP
        else:
            raise ValueError(f"Value {value} not a bool")
        self._raise_data_error()
        self._set_pin()

    @property
    def pull_down(self):
        ''' Return TRUE if pin has pull-up set '''
        return PIN_PULL_TEXT.index(self._data.get('pull')) == PULL_DOWN

    @pull_down.setter
    def pull_down(self, value):
        if isinstance(value, bool):
            self._data['pull'] = PULL_DOWN
        else:
            raise ValueError(f"Value {value} not a bool")
        self._raise_data_error()
        self._set_pin()

    def _set_pin(self, mode:None|int|str=None, pull:None|int|str=None):
        ''' Create a message to send to the server to set the pin parameters '''

    @property
    def is_pwm(self):
        ''' Return TRUE if the pin is configured as a PWM '''
        return self._data['mode'] == ALT and self._data['alt'] == PWM

    def __iter__(self):
        yield from self._data.items()

    def __str__(self):
        data = {key: value for key, value in self._data.items()} #manual deep copy due to Micropython
        data['mode'] = PIN_MODE_TEXT[data['mode']]
        if 'pull' in data:
            data['pull'] = PIN_PULL_TEXT[data['pull']] if data['pull'] is not None else None
        data['alt'] = PIN_ALT_TEXT[data['alt']] if isinstance(data['alt'], int) and data['alt'] < len(PIN_ALT_TEXT) else data['alt']
        return str(data)
