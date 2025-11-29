from re import search as re_search
from machine import PWM, Pin # type: ignore # pylint: disable=E0401
from microcom.log_manager import logging_handler
from microcom.exceptions import MicrocomException

REPR_PWM_RE_SLICE = r'slice=([0-9]+)'
REPR_PWM_RE_CHANNEL = r'channel=([0-9]+)'
REPR_PWM_RE_INVERT = r'invert=([0-1])'


async def pwm_read(pin_list:list, _logger:logging_handler, _vars_pwm:dict) -> dict:
    ''' Read a PWM Pin, if the Pin is not configured for PWM returns an error '''
    pin_data = {str(x):{} for x in pin_list}
    for pin in pin_list:
        pin = str(pin)
        if pin not in _vars_pwm:
            raise MicrocomException(f"Pin {pin} not configured for PWM")
        slice_re = re_search(REPR_PWM_RE_SLICE, repr(_vars_pwm[pin]))
        channel_re = re_search(REPR_PWM_RE_CHANNEL, repr(_vars_pwm[pin]))
        invert_re = re_search(REPR_PWM_RE_INVERT, repr(_vars_pwm[pin]))
        freq = _vars_pwm[pin].freq()
        duty_u16 = _vars_pwm[pin].duty_u16()
        pin_data[pin] = {'slice': int(slice_re.groups()[0]) if slice_re else None,
                'channel': int(channel_re.groups()[0]) if channel_re else None,
                'invert': bool(int(invert_re.groups()[0])) if invert_re else None,
                'freq': freq,
                'duty_u16': duty_u16}
        _logger.debug(f"PWM_READ: Pin {pin}: {pin_data[pin]}")
    return pin_data


async def pwm_set(pins:dict, _logger:logging_handler, _vars_pwm:dict) -> dict:
    ''' Write the config for a Pin '''
    for pin in pins:
        # check if PWM object already acquired
        new_config = {x:pins[pin][x] for x in pins[pin] if x in ['freq', 'duty_u16']}
        _vars_pwm[pin] = _vars_pwm.get(pin, PWM(Pin(int(pin)), **new_config))
        _logger.info(f"PWM_SET: Pin {pin} set: {new_config}")
        _vars_pwm[pin].init(**new_config)
    return await pwm_read([int(x) for x in pins], _logger, _vars_pwm)
