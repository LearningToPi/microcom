# pylint: disable=E0401,C0415
''' Platform specific file for RP2 based microcontrollers '''
from machine import ADC # pyright: ignore[reportMissingImports]
from microcom.msg._base import MicrocomMsg
from microcom.exceptions import MicrocomException, MicrocomPIOException
from microcom.gpio import GPIO_GLOBAL_CONSTANTS
from libs.logging_handler import logging_handler, INFO

# Global platform variables
PLATFORM_SUPPORTED_BUSES = ('i2c',)
PLATFORM_SUPPORTED_FUNCTIONS = ('pio_read_pwm_setup', 'pio_read_pwm_delete', 'pio_read_pwm'
                           'pio_read_tach_setup', 'pio_read_tach_delete', 'pio_read_tach')

PLATFORM_SUPPORTED_IRQ_FUNCTIONS = ('pio_read_pwm', 'pio_read_tach')

PIO_DEFAULT_FREQ = 100_000
PIO_SM_MAX = 8

# GPIO Constants
class GPIO_CONSTANTS(GPIO_GLOBAL_CONSTANTS):
    ''' Class to hold platform specific constants '''

# CPU Temp
CPU_TEMP_ADC = ADC(ADC.CORE_TEMP)
TEMP_CONVERSION_FACTOR = 3.3 / (65535)

def rp2_cpu_temp() -> dict:
    ''' return the temp of the CPU for the RP2 '''
    return {'cpu': round(27 - ((CPU_TEMP_ADC.read_u16() * TEMP_CONVERSION_FACTOR) - 0.706) / 0.001721, 2), 'cpu_temp_scale': 'c'}


async def handler(message:MicrocomMsg, _vars:dict, _logger:logging_handler):
    ''' Handle a platform specific call '''
    if not isinstance(message.data, dict) or 'function' not in message.data:
        raise MicrocomException(f"RP2 Platform Specific handler expecting a dict with 'function' value, got: {message.data}")
    if 'sm' not in _vars['platform']:
        # setup a list of state machines
        _vars['platform']['sm'] = [None] * PIO_SM_MAX

    if message.data['function'] == 'pio_read_pwm_setup':
        import microcom.rp2.pio_pwm
        if message.data.get('sm', None) is None:
            if None not in _vars['platform']['sm']:
                raise MicrocomPIOException("RP2 PIO PWM READ No state machine given and no state machines free")
            sm_id = _vars['platform']['sm'].index(None)
        else:
            sm_id = message.data['sm']
        _logger.info(f"Creating RP2 PIO PWM on StateMachine {sm_id}  using {message.data}")
        _vars['platform']['sm'][sm_id] = microcom.rp2.pio_pwm.PioPWM(
            pin=message.data['pin'], sm_id=sm_id,
            freq=message.data.get('freq', PIO_DEFAULT_FREQ),
            active=message.data.get('active', True),
            log_level=message.data.get('log_level', INFO)
        )
        # return the state machine number that was assigned
        return sm_id

    elif message.data['function'] == 'pio_read_pwm_delete':
        # stop and delete the PIO state machine
        if 'sm' not in message.data:
            raise MicrocomPIOException(f"RP2 PIO PWM READ Cannot stop PIO PWM without a 'sm', got: {message.data}")
        if _vars['platform']['sm'][message.data['sm']] is None:
            raise MicrocomPIOException(f"Cannot stop PIO State Machine {message.data['sm']}. State machine not active.")
        _logger.info(f"Stopping state machine {message.data['sm']}")
        _vars['platform']['sm'][message.data['sm']].stop()
        _vars['platform']['sm'][message.data['sm']] = None
        # return True if the PIO was successfully deactivated
        return True

    elif message.data['function'] == 'pio_read_pwm':
        # read the pwm value
        if 'sm' not in message.data:
            raise MicrocomPIOException(f"RP2 PIO PWM READ Cannot read PIO PWM without a 'sm', got: {message.data}")
        if _vars['platform']['sm'][message.data['sm']] is None:
            raise MicrocomPIOException(f"RP2 PIO PWM READ Cannot read PIO State Machine {message.data['sm']}. State machine not active.")
        _logger.info(f"RP2 PIO_READ_PWM reading StateMachine {message.data['sm']}")
        if message.data.get('get', 'u16') == 'percent':
            return await _vars['platform']['sm'][message.data['sm']].get_duty_percent(
                count=message.data.get('count', 10),
                wait=message.data.get('wait', True),
                clear_rx_buffer=message.data.get('clear_rx_buffer', True)
            )
        else: # if not percent, return u_16 value
            return await _vars['platform']['sm'][message.data['sm']].get_duty_u16(
                count=message.data.get('count', 10),
                wait=message.data.get('wait', True),
                clear_rx_buffer=message.data.get('clear_rx_buffer', True)
            )

    elif message.data['function'] == 'pio_read_tach_setup':
        # setup the tach
        import microcom.rp2.pio_pwm
        if message.data.get('sm', None) is None:
            if None not in _vars['platform']['sm']:
                raise MicrocomPIOException("RP2 PIO TACH No state machine given and no state machines free")
            sm_id = _vars['platform']['sm'].index(None)
            if 'pulse_per_rev' not in message.data:
                raise MicrocomPIOException(f"RP2 PIO TACH requires 'pulse_per_rev' to configure, got: {message.data}")
        else:
            sm_id = message.data['sm']
        _logger.info(f"Creating RP2 PIO TACH on StateMachine {sm_id} using {message.data}")
        _vars['platform']['sm'][sm_id] = microcom.rp2.pio_pwm.PioTach(
            pin=message.data['pin'], sm_id=sm_id,
            pulse_per_rev=message.data['pulse_per_rev'],
            freq=message.data.get('freq', PIO_DEFAULT_FREQ),
            active=message.data.get('active', True),
            log_level=message.data.get('log_level', INFO)
        )
        return sm_id

    elif message.data['function'] == 'pio_read_tach_delete':
        # stop and delete the PIO state machine
        if 'sm' not in message.data:
            raise MicrocomPIOException(f"RP2 PIO TACH Cannot stop without a 'sm', got: {message.data}")
        if _vars['platform']['sm'][message.data['sm']] is None:
            raise MicrocomPIOException(f"RP2 PIO TACH Cannot stop PIO State Machine {message.data['sm']}. State machine not active.")
        _logger.info(f"Stopping state machine {message.data['sm']}")
        _vars['platform']['sm'][message.data['sm']].stop()
        _vars['platform']['sm'][message.data['sm']] = None
        return True

    elif message.data['function'] == 'pio_read_tach':
        # read the tach rpm value
        if 'sm' not in message.data:
            raise MicrocomPIOException(f"RP2 PIO TACH Cannot read PIO PWM without a 'sm', got: {message.data}")
        if _vars['platform']['sm'][message.data['sm']] is None:
            raise MicrocomPIOException(f"RP2 PIO TACH Cannot read PIO State Machine {message.data['sm']}. State machine not active.")
        _logger.info(f"RP2 PIO_READ_TACH reading StateMachine {message.data['sm']}")
        return await _vars['platform']['sm'][message.data['sm']].get_rpm(
            count=message.data.get('count', 10),
            wait=message.data.get('wait', True),
            clear_rx_buffer=message.data.get('clear_rx_buffer', True)
        )
