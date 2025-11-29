'''
How the Pfc8595 works:
There are two modes:
    1. light pull-up (100K ohm):
        As an output = 1
        As an input, external device can pull to ground to make it "0", othwise reads as 1
    2. 20mA sink open drain
        As an output = 0
        Will always read 0, so don't use as an input

'''
from logging_handler import create_logger, INFO
from time import time, sleep
from typing import Callable
from microcom.exceptions import *
from microcom.gpio import GeneralGPIO, PULL_UP, IN, OUT


VALID_PIN_MODE = (IN, OUT)
PIN_MODE_TEXT = ('IN', 'OUT')

PULL_NONE = None
VALID_PIN_PULL = (PULL_NONE,)
PIN_PULL_TEXT = (None,)


GPIO_BIT_MAP = (7, 6, 5, 4, 3, 2, 1, 0, 17, 16, 15, 14, 13, 12, 11, 10)
GPIO_BIT_OFFSET = (15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0)


class Pfc8575GPIO(GeneralGPIO):
    ''' Class to represent a GPIO attached to the PFC8575 I2C expansion board '''
    def __init__(self, pin:int, read_func:Callable, set_func:Callable, log_level:str=INFO):
        self._read_func, self._set_func = read_func, set_func
        super().__init__(pin=pin, log_level=log_level)
        self.local_mode = IN
        self.local_value = 1

    def read(self):
        ''' Read the current state of the GPIO '''
        self._read_func()
        return {'mode': self.local_mode, 'value': self.local_value, 'pull': 0}

    def set(self, update_now:bool=True, **kwargs):
        ''' Write the value or mode to the GPIO '''
        if 'pull' in kwargs and kwargs['pull'] not in VALID_PIN_PULL:
            raise ValueError(f"Pull '{kwargs['pull']}' not in valid pull values: {VALID_PIN_PULL}")

        if 'mode' in kwargs:
            if kwargs['mode'] not in VALID_PIN_MODE:
                raise ValueError(f"Mode '{kwargs['mode']}' not in supported mode values: {VALID_PIN_MODE}")
            self.local_mode = kwargs['mode']

        if 'value' in kwargs:
            if kwargs['value'] not in [0, 1, True, False]:
                raise ValueError(f"Value '{kwargs['value']}' not in valid values: {[0, 1, True, False]}")
            if self.local_mode != OUT:
                raise NotImplementedError('Cannot set value unless in OUT mode')
            self.local_value = kwargs['value']

        # only write if update_now is set
        if update_now:
            self._set_func()


class Pfc8575I2C:
    ''' Class to represent and ADS111x I2C based sensor (i.e. ADS1115) '''
    def __init__(self, address:int, bus_id:int, read_func:Callable, write_func:Callable, log_level=INFO, irq_register:Callable|None=None):
        self._logger = create_logger(log_level, name=f"ADS111x({address})")
        self._address, self._bus_id, self.read, self.write, self._irq_register = address, bus_id, read_func, write_func, irq_register
        self._interrupt_pin = None
        # initialize all ports high as input - 65535 is all 16 bits high
        self.pins = [Pfc8575GPIO(pin=x,
                                  read_func=self.read_all,
                                  set_func=self.write_all,
                                  log_level=log_level) for x in GPIO_BIT_MAP]
        self.write_all()

    def setup_interrupt(self, interrupt_pin:GeneralGPIO, interrupt_handler:Callable):
        ''' Configure the interrupt pin. INT pin on PFC8575 will pull LOW when a change has been identified and is held until next read '''
        self._interrupt_pin = interrupt_pin
        self._interrupt_pin.mode = IN
        self._interrupt_pin.pull = PULL_UP
        self._interrupt_pin.set_irq(irq_handler=interrupt_handler,
                                    rising=False,
                                    falling=True,
                                    message=False,
                                    execute={'execute': 'bus_cmd',
                                             'bus_type': 'I2C',
                                             'bus_id': self._bus_id,
                                             'bus_cmd': 'readfrom',
                                             'cmd_params': {'addr': self._address, 'nbytes': 2}})

    def read_all(self):
        ''' Read the current state from the device and update the pins '''
        # read from the device
        data = int.from_bytes(self.read(self._address, nbytes=2), 'big')
        self._logger.debug(f"Read {data} from PFC8575")
        for x in range(len(GPIO_BIT_MAP)): # pylint: disable=C0200
            # set the value for each pin object, mode is not changed
            self.get_gpio(GPIO_BIT_MAP[x]).local_value = (data >> (len(GPIO_BIT_MAP) - x - 1)) & 1 # -1 to fix index offset

    def write_all(self):
        ''' Write all pins based on the pin objects '''
        data = 0
        for x in range(len(GPIO_BIT_MAP)): # pylint: disable=C0200
            # set the value to 1 if output and high, or input
            write_val = 1 if self.get_gpio(GPIO_BIT_MAP[x]).local_mode == IN or self.get_gpio(GPIO_BIT_MAP[x]).local_value == 1 else 0
            if write_val == 1:
                data += write_val << (len(GPIO_BIT_MAP) - x - 1) # -1 to fix index offset
        self._logger.debug(f"Writing {data} to PFC8575")
        self.write(self._address, data=data.to_bytes(2, 'big'))

    #def get_all(self) -> int:
    #    ''' Read all port info '''
    #    return int.from_bytes(self.read(self._address, nbytes=2), 'big')
    #
    #def all_gpio_values(self) -> list[int]:
    #    ''' Return a list of all the GPIO pin values '''
    #    return_values = []
    #    pin_values = self.get_all()
    #    for pin in GPIO_BIT_MAP:
    #        return_values.append(int(pin_values & (1 << GPIO_BIT_OFFSET[GPIO_BIT_MAP.index(pin)]) > 0))
    #    return return_values
    #
    #def read_gpio(self, pin:int) -> int:
    #    ''' Return the current value from a GPIO (0 or 1) '''
    #    return int((self.get_all() & (1 << GPIO_BIT_OFFSET[GPIO_BIT_MAP.index(pin)])) > 0)
    #
    #def write_all(self, data:int):
    #    ''' Write the port info '''
    #    self.write(self._address, data=data.to_bytes(2, 'big'))

    #def current_set_value(self) -> int:
    #    ''' Return the int that matches the current set value '''
    #    value = 0
    #    for x in range(len(self.pins)): # pylint: disable=C0200
    #        value += (1 << x) if self.pins[x].current_mode == IN or (self.pins[x].current_mode == OUT and self.pins[x].set_value == 1) else 0
    #    return value

    #def set_gpio(self, pin:int, value:int):
    #    ''' Write the config for a pin '''
    #    if value not in [0, 1]:
    #        raise ValueError(f"Value '{value}' not supported for GPIO pin.")
    #    # reset the bit to 0 for the selected GPIO pin
    #    config_value = self.current_set_value() & ~(1 << GPIO_BIT_OFFSET[GPIO_BIT_MAP.index(pin)])
    #    config_value |= value << GPIO_BIT_OFFSET[GPIO_BIT_MAP.index(pin)]
    #    self.write_all(config_value)

    def get_gpio(self, pin:int, mode:int|None=None, value:int|None=None) -> Pfc8575GPIO:
        ''' Get a GPIO object for a pin '''
        gpio = self.pins[GPIO_BIT_MAP.index(pin)]
        if mode is not None:
            gpio.mode = mode
        if mode is OUT and value is not None:
            gpio.value = value
        return gpio


#class Pfc8575I2C_Multiplexed(Pfc8575I2C):
#    ''' Class to handle a pfc8575 that has been multiplexed using a Pca9548a I2C expander '''
#    def __init__(self, channel_cmd:Callable, address:int, bus_id:int, read_func: Callable, write_func: Callable, log_level=INFO, irq_register:Callable|None=None):
#        super().__init__(address, bus_id, read_func, write_func, log_level, irq_register)
#        self._channel_cmd = channel_cmd
#
#    def setup_interrupt(self, interrupt_pin:GeneralGPIO, interrupt_handler:Callable):
#        ''' Configure the interrupt pin. When multiplexed, the multiplexer must first be set to the correct channel '''
#        self._interrupt_pin = interrupt_pin
#        self._interrupt_pin.mode = IN
#        self._interrupt_pin.pull = PULL_UP
#        self._interrupt_pin.set_irq(irq_handler=interrupt_handler,
#                                    rising=False,
#                                    falling=True,
#                                    message=False,
#                                    execute=[self._channel_cmd(),
#                                            {'execute': 'bus_cmd',
#                                             'bus_type': 'I2C',
#                                             'bus_id': self._bus_id,
#                                             'bus_cmd': 'readfrom',
#                                             'cmd_params': {'addr': self._address, 'nbytes': 2}}])