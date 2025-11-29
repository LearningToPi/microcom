from typing import Callable
import json
from logging_handler import create_logger, INFO
from microcom.msg._base import MicrocomMsg, MSG_TYPE_NEOPIXEL_CMD, MSG_TYPE_GET_VAR
from microcom.exceptions import MicrocomException


NEOPIXEL_DEVICE_KEYS = ['pin', 'name', 'count', 'rgbw', 'timing800', 'max_brightness']


class NeoColor:
    ''' Class to represent a color object '''
    def __init__(self, r:int=255, g:int=255, b:int=255, repeat:int=1):
        if r > 255 or r < 0 or not isinstance(r, int):
            raise ValueError(f"Red must be between 0 and 255, got {r}")
        if g > 255 or g < 0 or not isinstance(g, int):
            raise ValueError(f"Green must be between 0 and 255, got {g}")
        if b > 255 or b < 0 or not isinstance(b, int):
            raise ValueError(f"Blue must be between 0 and 255, got {b}")
        if repeat <= 0 or not isinstance(repeat, int):
            raise ValueError(f"Repeat must be a positive integer, got {repeat}")
        self.rgb = [r, g, b]
        self.repeat = repeat

    @property
    def r(self):
        ''' Red '''
        return self.rgb[0]
    @property
    def g(self):
        ''' green '''
        return self.rgb[1]
    @property
    def b(self):
        ''' blue '''
        return self.rgb[2]

    @r.setter
    def r(self, r):
        if r > 255 or r < 0 or not isinstance(r, int):
            raise ValueError(f"Red must be between 0 and 255, got {r}")
        self.rgb[0] = r
    @g.setter
    def g(self, g):
        if g > 255 or g < 0 or not isinstance(g, int):
            raise ValueError(f"Green must be between 0 and 255, got {g}")
        self.rgb[1] = g
    @b.setter
    def b(self, b):
        if b > 255 or b < 0 or not isinstance(b, int):
            raise ValueError(f"Blue must be between 0 and 255, got {b}")
        self.rgb[2] = b

    def __repr__(self):
        return f"{self.__class__.__name__}(red={self.r}, green={self.g}, blue={self.b}, repeat={self.repeat})"

    def __str__(self):
        return self.__repr__()

    def tuple(self) -> tuple:
        ''' return the class as a tuple object for use in a MicrocomMsg '''
        return ((self.rgb[0], self.rgb[1], self.rgb[2]), self.repeat)

    def dict(self) -> list:
        ''' Return the dict friendly version '''
        return [[self.rgb[0], self.rgb[1], self.rgb[2]], self.repeat]



class NeoTask:
    ''' Class to represent a generic task to execute for a NeoPixel light string group '''
    _EXCLUDE = ['dict', 'message']
    _task = None

    def __repr__(self):
        return f"{self.__class__.__name__}({', '.join([str(key) + '=' + str(val) for key, val in self.dict().items()])})"

    def __str__(self):
        return self.__repr__()

    def dict(self) -> dict:
        ''' return the task as a dict object '''
        return {x: getattr(self, x) for x in dir(self) if not x.startswith('_') and x not in self._EXCLUDE}

    def message(self):
        ''' return the task as a dict formatted for a MicrocomMsg '''
        data = {'cmd': self._task, 'kwargs': self.dict()}
        for key, value in data['kwargs'].items():
            if isinstance(value, NeoColor):
                data['kwargs'][key] = value.tuple()
            if isinstance(value, list):
                for x in range(len(value)): #pylint: disable=C0200
                    if isinstance(value[x], NeoColor):
                        value[x] = value[x].tuple()
        return data

class NeoPattern(NeoTask):
    ''' Class to represent a pattern of colors on a NeoPixel '''
    def __init__(self, color_list:list[NeoColor]):
        self.pattern = color_list
        self._task = 'pattern'


class NeoRandom(NeoTask):
    ''' Class to represent setting a NeoPixel device to random colors per pixel '''
    def __init__(self, max_brightness:int=255):
        self.max_brightness = max_brightness
        self._task = 'random'


class NeoFade(NeoTask):
    ''' Class to represent a fade task for a NeoPixel device '''
    def __init__(self, pattern:list[NeoColor], jump:int=1, delay_ms:int=100, color_pause_ms:int=100, repeat:int=1, clear_end:bool=False):
        self.pattern, self.jump, self.delay_ms, self.color_pause_ms, self.repeat, self.clear_end = pattern, jump, delay_ms, color_pause_ms, repeat, clear_end
        self._task = 'fade'


class NeoShift(NeoTask):
    ''' Class to represent a shift of the lights on a NeoPixel device '''
    def __init__(self, count:int=1, repeat:int=1, clear_end:bool=False, interval:int=100):
        self.count, self.repeat, self.clear_end, self.interval = count, repeat, clear_end, interval
        self._task = 'shift'


class NeoKitt(NeoTask):
    ''' class to represent a kitt task for a NeoPixel device '''
    def __init__(self, pattern:NeoPattern, fwd_pattern:NeoColor, rev_pattern:NeoColor, interval:int=100, end_swipe_interval:int=100, repeat:int=1, jump:int=1, clear_start:bool=False, clear_end:bool=False):
        self.pattern, self.fwd_pattern, self.rev_pattern, self.interval, self.end_swipe_interval, self.repeat, self.jump, self.clear_start, self.clear_end = pattern, fwd_pattern, rev_pattern, interval, end_swipe_interval, repeat, jump, clear_start, clear_end
        self._task = 'kitt'


class MicrocomNeoPixel:
    ''' Class to represent one or more NeoPixel strings attached to a Microcomserver.  Multiple strings can be used together for larger patterns and actions '''
    def __init__(self, devices:list[dict], send:Callable, name:str, log_level:str=INFO, _create:bool=True):
        ''' Initialize one or more NeoPixel strings, devices must be a list of dict objects. The dict contains:
        {
            pin (int) [REQUIRED] - GPIO pin the string is attached to
            count (int) [REQUIRED] - number of neopixel devices in the string
            name (str) [optional] - Name for the string
            rgbw (bool) [optional] (default False) - True if string is 4 byte with R/G/B/W else 3 bytes R/G/B
            timing800 (bool) [optional] (default True) - True for 800kHz or False for 400kHz.  Most are 800
            max_brightness (int) [optional] (default 255) - Set a max brightness for the string (0-255)
        }
        '''
        for device in devices:
            if 'pin' not in device or 'count' not in device:
                raise ValueError(f"Expecting a 'pin' and 'count' parameter, got: {device}")
            for key in device:
                if key not in NEOPIXEL_DEVICE_KEYS:
                    raise ValueError(f"Expecting only keys: {NEOPIXEL_DEVICE_KEYS}, got {key}")
            if 'name' not in device:
                device['name'] = str(device['pin'])
        self._logger = create_logger(log_level, name=f"{self.__class__.__name__}({[x['name'] for x in devices]})")
        self._devices, self._send, self.name, self.log_level = devices, send, name, log_level
        if _create:
            self.create()

    @classmethod
    def from_server(cls, name:str, send:Callable, log_level:str=INFO):
        ''' Get a Neopixel device from the server using the name provided '''
        device = cls(name=name, devices=[], send=send, log_level=log_level, _create=False)
        message = MicrocomMsg(msg_type=MSG_TYPE_GET_VAR, data={'var_type': 'neo', 'var_name': name})
        device._logger.info(f"Getting NeoPixel device '{name}'...")
        device._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error Getting NeoPixel {name}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        device._devices = json.loads(message.reply_msg.data[name])['devices']
        return device

    def __repr__(self):
        return f"{self.__class__.__name__}(devices={self._devices})"

    def create(self):
        ''' Create the NeoPixel devices on the Microcom server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'INIT', 'kwargs': {'devices': self._devices, 'log_level': self.log_level}}})
        self._logger.info("Creating NeoPixel devices...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error creating NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def destroy(self):
        ''' Delete a Neopixel device on  the Microcom server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'DELETE'}})
        self._logger.info("Deleting NeoPixel devices...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error destroying NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def clear(self):
        ''' Clears all the NeoPixel devices. '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name:{'cmd': 'clear'}})
        self._logger.info("Deleting NeoPixel devices...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error clearing NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

#    def pattern(self, pattern:list[NeoColor], start:int=0, reverse:bool=False, fill:bool=True):
#        ''' Write a pattern on the group of devices '''
#        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'pattern', 'kwargs': {'pattern': _convert_NeoColor_list(pattern), 'start': start,
#                                                                                                             'reverse': reverse, 'fill': fill}}})
#        self._logger.info("Writing NeoPixel pattern...")
#        self._logger.debug(f"Neopixel pattern: {pattern}, start: {start}, reverse: {reverse}, fill: {fill}")
#        self._send(message, wait_for_reply=True)
#        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
#            raise MicrocomException(f"Error running pattern on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
#                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
#
#    def random(self, max_brightness:int=255):
#        ''' Write random values to the LEDs '''
#        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'random', 'kwargs': {'max_brightness': max_brightness}}})
#        self._logger.info("Writing NeoPixel random...")
#        self._send(message, wait_for_reply=True)
#        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
#            raise MicrocomException(f"Error running random on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
#                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
#
#    def fade(self, pattern:list[NeoColor], jump:int=1, delay_ms:int=0, color_pause_ms:int=0, repeat:int=2, clear_end:bool=False):
#        ''' Fade the string of lights from one color to the next using the supplied list of colors '''
#        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'fade', 'kwargs': {'pattern': _convert_NeoColor_list(pattern), 'jump': jump, 'delay_ms': delay_ms,
#                                                                                                           'color_pause_ms': color_pause_ms, 'repeat': repeat, 'clear_end': clear_end}}})
#        self._logger.info("Writing NeoPixel fade...")
#        self._logger.debug(f"NeoPixel Fade: {pattern}, jump: {jump}, delay_ms: {delay_ms}, color_pause_ms: {color_pause_ms}, repeat: {repeat}, clear_end: {clear_end}")
#        self._send(message, wait_for_reply=True)
#        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
#            raise MicrocomException(f"Error running fade on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
#                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
#
#    def shift(self, count:int=1, devices:list|bool=True, repeat:int=1, clear_end:bool=False, interval:int=250):
#        ''' Shift the pattern on the strings by 'count' amount 'repeat' times with a delay of 'interval' milliseconds. '''
#        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'shift', 'kwargs': {'count': count, 'devices': devices, 'repeat': repeat,
#                                                                                                          'clear_end': clear_end, 'interval': interval}}})
#        self._logger.info("Writing NeoPixel shift...")
#        self._logger.debug(f"NeoPixel Shift: {count}, repeat: {repeat}, clear_end: {clear_end}, interval: {interval}ms")
#        self._send(message, wait_for_reply=True)
#        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
#            raise MicrocomException(f"Error running shift on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
#                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
#
#    def kitt(self, pattern:list[NeoColor], fwd_pattern:NeoColor, rev_pattern:NeoColor, devices:list|bool=True, interval:int=250, end_swipe_interval:int=250, repeat:int=1, jump:int=1,
#             clear_start:bool=True, clear_end:bool=False):
#        ''' run the kitt pattern '''
#        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'kitt', 'kwargs': {'pattern': _convert_NeoColor_list(pattern),
#                                                                                                          'fwd_pattern': [fwd_pattern.r, fwd_pattern.g, fwd_pattern.b],
#                                                                                                          'rev_pattern': [rev_pattern.r, rev_pattern.g, rev_pattern.b], 'devices': devices,
#                                                                                                          'interval': interval, 'end_swipe_interval': end_swipe_interval, 'repeat': repeat,
#                                                                                                          'jump': jump, 'clear_start': clear_start, 'clear_end': clear_end}}})
#        self._logger.info("Writing NeoPixel kitt...")
#        self._logger.debug(f"NeoPixel Kitt: {pattern}, fwd_pattern: {fwd_pattern}, rev_pattern: {rev_pattern} jump: {jump}, repeat: {repeat}, interval: {interval}, "
#                           f"end_swipe_interval: {end_swipe_interval}, clear_start: {clear_start}, clear_end: {clear_end}")
#        self._send(message, wait_for_reply=True)
#        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
#            raise MicrocomException(f"Error running kitt on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
#                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def run_task(self, task:NeoTask):
        ''' Execute a neopixel task '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: task.message()})
        self._logger.info(f"Starting task type '{task._task}'")
        self._logger.debug(f"NeoPixel message data: {message.data}")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error running task on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def run_program(self, program_list:list):
        ''' Run a program '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'run_program', 'kwargs': {'program_list': program_list}}})
        self._logger.info("Starting Program...")
        self._logger.debug(f"NeoPixel Run Program: {program_list}")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error running program on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def end_program(self, clear:bool=True):
        ''' Terminate a running program '''
        message = MicrocomMsg(msg_type=MSG_TYPE_NEOPIXEL_CMD, data={self.name: {'cmd': 'end_program', 'kwargs': {'clear': clear}}})
        self._logger.info("Starting Program...")
        self._logger.debug("NeoPixel End Program...")
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0): # type: ignore
            raise MicrocomException(f"Error ending program on NeoPixel {self._devices}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, "
                                    f"error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")


#def _convert_NeoColor_list(data:list[NeoColor]):
#    ''' Convert a list of NeoColor objects to the tuples used by the microcom server '''
#    return_data = []
#    for color in data:
#        return_data.append([[color.r, color.g, color.b], color.repeat])
#    return return_data

