# pylint: disable=C0415
'''
Microcom GPIO level functions
'''
import asyncio
from re import search as re_search
from machine import Pin # type: ignore # pylint: disable=E0401
from microcom.log_manager import logging_handler


REPR_PIN_RE_NAME = r'Pin\((GPIO[0-9]+)[,\)]'
REPR_PIN_RE_MODE = r'mode=([a-zA-Z0-9_]+)[,\)]'
REPR_PIN_RE_PULL = r'pull=([a-zA-Z0-9_]+)[,\)]'
REPR_PIN_RE_ALT = r'alt=([a-zA-Z0-9_]*)[,\)]'

class GPIO_GLOBAL_CONSTANTS:
    ''' Class to hold platform specific constants - Defaults to RP2, override on a per platform basis '''
    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    ALT = 3
    PULL_NONE = None
    PULL_UP = 1
    PULL_DOWN = 2
    VALID_PIN_MODE = (IN, OUT, OPEN_DRAIN, ALT)
    PIN_MODE_TEXT = ('IN', 'OUT', 'OPEN_DRAIN', 'ALT')
    VALID_PIN_PULL = (PULL_NONE, PULL_UP, PULL_DOWN)
    PIN_PULL_TEXT = ('NONE', 'PULL_UP', 'PULL_DOWN')
    DEBOUNCE_MS = 200
    PWM = 'pwm'
    I2C = 'i2c'
    VALID_PIN_ALT = (PWM, I2C)
    PIN_ALT_TEXT = (None, 'SPI', 'UART', 'I2C', 'PWM', 'SIO', 'PIO0', 'PIO1', 'GPCK', 'USB', '31')
    
    DEFAULT_MODE = 'IN'
    DEFAULT_PULL = None
    _SKIP_PROPS = ['dict', 'get_mode', 'get_pull']
    
    def __str__(self):
        return f"{__class__.__name__}({', '.join([str(x) + '=' + str(getattr(self, x)) for x in dir(self) if not x.startswith('_') and x not in self._SKIP_PROPS])})"
    
    def dict(self):
        ''' Return the contents of the class as a dictionary '''
        return {str(x): getattr(self, x) for x in dir(self) if not x.startswith('_') and x not in self._SKIP_PROPS}
    
    def get_mode(self, key):
        ''' Return the proper constant value for the passed mode '''
        return self.VALID_PIN_MODE[self.PIN_MODE_TEXT.index(str(key).upper())]
    
    def get_mode_text(self, value):
        ''' Return the text version of the mode '''
        return self.PIN_MODE_TEXT[self.VALID_PIN_MODE.index(value)]
    
    def get_pull(self, key):
        ''' Return the proper constant value for the pull '''
        return self.VALID_PIN_PULL[self.PIN_PULL_TEXT.index(str(key).upper())]
    
    def get_pull_text(self, value):
        ''' Return the text version of the pull, return the object None in place of 'None' '''
        temp = self.PIN_PULL_TEXT[self.VALID_PIN_PULL.index(value)]
        return None if temp == 'None' else temp


async def gpio_get(pins:list, _logger:logging_handler, CONST:GPIO_GLOBAL_CONSTANTS) -> dict:
    ''' Read one or more GPIO pins and return the data
       pin (list) - list of integers matching the GPIO pin assignment '''
    pin_data = {}
    for pin in pins:
        name_re = re_search(REPR_PIN_RE_NAME, repr(Pin(pin)))
        mode_re = re_search(REPR_PIN_RE_MODE, repr(Pin(pin)))
        pull_re = re_search(REPR_PIN_RE_PULL, repr(Pin(pin)))
        alt_re = re_search(REPR_PIN_RE_ALT, repr(Pin(pin)))
        value = Pin(pin).value()
        pin_data[str(pin)] = {'name': name_re.groups()[0] if name_re else None,
                'pin': int(name_re.groups()[0].replace('GPIO', '')) if name_re else None,
                'mode': CONST.get_mode_text(mode_re.groups()[0]) if mode_re else None,
                'pull': CONST.get_pull_text(pull_re.groups()[0]) if pull_re else None,
                'alt': alt_re.groups()[0] if alt_re else None,
                'value': value}
        _logger.debug(f"GPIO_GET: Read pin {pin}: {pin_data[str(pin)]}")
    return pin_data


async def gpio_set(pins:dict, _logger:logging_handler, CONST:GPIO_GLOBAL_CONSTANTS):
    ''' Configure one or more GPIO pins, then read and return
        pins (dict) -
          '[pin_int]' (dict) -
              ... pin arguments ... '''
    # read the pin data first, the init function needs ALL the values passed
    pin_data = await gpio_get([int(x) for x in pins.keys()], _logger, CONST)
    for pin in pins:
        if pins[pin].get('mode', CONST.DEFAULT_MODE).upper() not in CONST.PIN_MODE_TEXT:
            raise ValueError(f"Pin config {pins[pin]} has invalid mode. Valid modes: {CONST.PIN_MODE_TEXT}")
        if str(pins[pin].get('pull', CONST.DEFAULT_PULL)).upper() not in CONST.PIN_PULL_TEXT and pins[pin].get('pull', None) is not None:
            raise ValueError(f"Pin config {pins[pin]} has invalid pull. Valid modes: {CONST.PIN_PULL_TEXT}")
        # update the existing values with the new values then apply
        pin_data[pin].update(**pins[pin])
        # set mode and pull to the platform specific value
        new_config = {'mode': CONST.get_mode(pins[pin].get('mode', CONST.DEFAULT_MODE)),
                      'pull': CONST.get_pull(pins[pin].get('pull', CONST.DEFAULT_PULL)),
                      'value': pins[pin].get('value', 0)}
        _logger.info(f"GPIO_SET: Setting pin {pin} with: {new_config}")
        Pin(int(pin)).init(mode=CONST.get_mode(pins[pin].get('mode', CONST.DEFAULT_MODE)), pull=CONST.get_pull(pins[pin].get('pull', CONST.DEFAULT_PULL)))

        Pin(int(pin)).init(**new_config)
    # update the data and return it
    return await gpio_get([int(x) for x in pins.keys()], _logger, CONST)


class PinIRQ:
    ''' Class to handle IRQ events for a pin
     tasks (list - [
        (tuple) (
            function (async generator) - supported async function from IRQ_COMMANDS
            (dict) - dict of kwargs to pass to the function
            (str) - kwarg to pass the pin IRQ data (if None, the pin data will not be passed)
        )
     ]) '''
    def __init__(self, pin:Pin, _logger:logging_handler, CONST:GPIO_GLOBAL_CONSTANTS, rising:bool=True, falling:bool=True, tasks:list|None=None,
                 debounce_ms:int|None=None):
        self.pin, self.rising, self.falling, self.tasks, self.CONST, self._logger = pin, rising, falling, tasks, CONST, _logger
        self.debounce_ms = debounce_ms if debounce_ms is not None else CONST.DEBOUNCE_MS # allow overriding platform default
        from asyncio import Lock, ThreadSafeFlag, sleep, create_task  # type: ignore #pylint: disable=E0611
        self._lock, self._tsf, self._sleep, self._create_task, self._irq_value = Lock(), ThreadSafeFlag(), sleep, create_task, None
        # set IRQ exception buffer
        import micropython # type: ignore # pylint: disable=E0401
        micropython.alloc_emergency_exception_buf(100)

        # create the irq pin handler
        trigger = 0
        trigger |= Pin.IRQ_RISING if self.rising else 0
        trigger |= Pin.IRQ_FALLING if self.falling else 0
        self._logger.info(f"PinIRQ Pin {self.pin} configuring IRQ, rising: {self.rising}, falling: {self.falling}")
        self.pin.irq(self._irq_handler, trigger=trigger)
        # start a handler loop thread
        asyncio.create_task(self._irq_handler_loop())

    def _irq_handler(self, pin:Pin):
        ''' Handle the interrupt and start the handler loop '''
        self._irq_value = pin.value()
        self._logger.debug(f"PinIRQ {self.pin}: IRQ Handler called for pin {pin}, value {pin.value()}")
        self._tsf.set()

    async def _irq_handler_loop(self):
        ''' Handle the IRQ from the Pin '''
        self._logger.debug(f"PinIRQ {self.pin}: Starting IRQ handler loop...")
        while True:
            try:
                # wait for the IRQ to activate the loop
                await self._tsf.wait()
                if self._lock.locked():
                    # if locked, then we are in the debounce, restart the loop
                    continue
                # start a debounce timer
                self._create_task(self._debounce_delay())

                # Execute the list of tasks
                for task in self.tasks if isinstance(self.tasks, list) else []:
                    # execute the async function, add the irq_value to the kwargs if a variable name is provided
                    self._logger.debug(f"PinIRQ {self.pin}: Calling tasks {task[0]}, params: {task[1]}, irq value: {self._irq_value}")
                    if task[2] is not None:
                        task[1][task[2]] = self._irq_value
                    self._create_task(task[0](**task[1]))
            except Exception as e: #pylint: disable=W0718
                self._logger.error(f"PinIRQ {self.pin}: Error in IRQ handler loop: {e.__class__.__name__}: {e}")

    async def _debounce_delay(self):
        ''' Start a debouce timer using the _lock '''
        await self._lock.acquire()
        await self._sleep(self.debounce_ms / 100)
        self._lock.release()
