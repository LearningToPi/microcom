from random import randrange
from _thread import start_new_thread, allocate_lock #as Lock
from utime import sleep_ms
from machine import Pin
from neopixel import NeoPixel
import uasyncio as asyncio
from json import loads
from time import time

from microcom.log_manager import logging_handler, INFO
from microcom.async_lock import Lock

__VERSION__ = (0,0,2)

STYLE_ALL = 10
STYLE_SEQ_DEV = 11
STYLE_SEQ_LED = 12
STYLE_NO_WRITE = 0
STYLES = {
    "0": "Style_No_Write",
    "10": "Style_All",
    "11": "Style_Device_Sequence",
    "12": "Style_LED_Sequence"
}
ALL_DEVS = None

NEOPX_ASYNC_WAIT_MS = 100

DEVICES_REQUIRED_FIELDS = ('pin', 'count', 'name', 'rgbw')

class MicrocomWS2811Device:
    ''' Class to represent a single string and hold relevant config and parameters '''
    def __init__(self, pin:int, count:int, name:str, rgbw:bool, timing800:bool, _logger:logging_handler, max_brightness:int=100):
        self.pin, self.count, self.name, self._logger, self.max_brightness = pin, count, name, _logger, max_brightness
        self._led_values = 4 if rgbw else 3
        self._logger.info(f"NeoPixel({name}): Initializing pin {pin}, count {count}, rgbw {rgbw}, 800kHz: {timing800}...")
        self.dev = NeoPixel(pin=Pin(pin),  # pylint: disable=E1123
                            n=count,
                            bpp=self._led_values,
                            timing=1 if timing800 else 0 # type: ignore
                            )

    def clear(self):
        ''' Clear a string '''
        self.dev.fill((0,0,0))
        self.dev.write()

    def pattern(self, pattern:tuple[tuple[tuple[int],int]], fill:bool=True):
        ''' Write a pattern to the light string. Pattern must be a list of tuples objects:
            pattern (tuple) (
                (
                    tuple: RGB(W) color to apply,
                    repeat: number of times to repeat the color
                )
            )'''
        # first convert the pattern to a string of bytes to write to the buffer
        pattern_bytes = b''.join([self._color_to_bytes(*x) for x in pattern])
        buf_pos = 0
        while buf_pos < len(self.dev.buf): # type: ignore # pylint: disable=E1101
            # write the pattern bytes to the buffer
            if buf_pos + self._led_values > len(self.dev.buf): # type:ignore # pylint: disable=E1101
                # if the next pattern will exceed the length of the string, remove the extra bytes
                pattern_bytes = pattern_bytes[0:len(self.dev.buf-buf_pos)] # type:ignore # pylint: disable=E1101
            self.dev.buf[buf_pos:buf_pos+len(pattern_bytes)] = pattern_bytes # type:ignore # pylint: disable=E1101
            if not fill:
                break

    def shift(self, count:int):
        ''' Shift all the lights either forward (positive count) or backwards (negative count) in the string'''
        if count >= 0:
            # get the bytes that are going to be overwritten at the end
            shift = bytes(self.dev.buf[len(self.dev.buf)-count*self._led_values:]) # type:ignore # pylint: disable=E1101
            # shuft the buffer backwardsd by the number of led's
            self.dev.buf[len(shift):] = self.dev.buf[0:len(self.dev.buf)-len(shift)] # type:ignore # pylint: disable=E1101
            # add the bytes that were overwritten to the start of the string
            self.dev.buf[0:len(shift)] = shift # type:ignore # pylint: disable=E1101
        else:
            # negative count
            count = abs(count)
            # get the bytes that are going to be overwritten at the beginning
            shift = bytes(self.dev.buf[0:count*self._led_values]) # type:ignore # pylint: disable=E1101
            # shift the buffer forward by the number of led's
            self.dev.buf[0:len(self.dev.buf)-len(shift)] = self.dev.buf[len(shift):] # type:ignore # pylint: disable=E1101
            # add the bytes that were overwritten to the end of the string
            self.dev.buf[len(self.dev.buf)-len(shift):len(self.dev.buf)-1] = shift # type:ignore # pylint: disable=E1101


    def _color_to_bytes(self, color:tuple[int], repeat:int) -> bytes:
        ''' Convert the color tuple into a bytes that can be inserted in the buffer, verify the brightness isn't exceeded '''
        scratch = list(color)
        for value in scratch:
            if value > 255 * self.max_brightness:
                value = int(255 * self.max_brightness)
        return b''.join([x.to_bytes(1, 'big') for x in scratch]) * repeat

class MicrocomWS2811Controller:
    ''' Class to represent one or more WS2811/2812 light strings 
        devices (list) [
            { pin (int): pin# for the light string,
              count (int): number of lights in the string,
              rgbw (bool): True if an RGBW string, false for RGB,
              timing800 (bool): True if 800kHz, False for 400kHz,
              name (str): Name of the string}
            ]'''
    def __init__(self, devices:list, log_level=INFO):
        self._logger = logging_handler(log_level)
        for device in devices:
            for field in DEVICES_REQUIRED_FIELDS:
                if field not in device:
                    raise ValueError(f"MicrocomWS2811 Device must include: {DEVICES_REQUIRED_FIELDS}, got {device}")
        self._devices = devices
        self._write_lock = Lock()

    def clear(self):
        ''' Clear the light strings '''

class NeoPixel_Controller:
    def __init__(self, devices:list, logger=logging, file_name=None):
        """ Run the workload - light controller """
        self._logger = logger if not isinstance(logger, int) else logging.create_logger(console_level=logger)
        self._logger.info('Starting NeoPixel Controller...')
        self.np_devices = []
        self._lock = Lock()
        self.thread_running = False
        self.task_running = False
        self.last_task = ''
        self.stop_thread = False
        self.stop_task = False
        self.longest_light = 0

        # setup light controllers
        for np_config in devices:
            if "pin" in np_config.keys() and "count" in np_config.keys() and isinstance(np_config['pin'], int) and isinstance(np_config['count'], int):
                device_name = np_config.get('name', f"NeoPixel_on_{np_config['pin']}")
                self.np_devices.append({'name': device_name, 'neo': NeoPixel(Pin(np_config['pin']), np_config['count'])})
                self._logger.info(f"Created NeoPixel device {device_name} on Pin {np_config['pin']} with {np_config['count']} LED's")
                self.longest_light = np_config['count'] if np_config['count'] > self.longest_light else self.longest_light
            else:
                self._logger.error(f"NeoPixel devices must have a Pin and Count field that are both integers.  Received {np_config}")

        # clear all lights to initialize
        asyncio.create_task(self.clear())
        asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)

        # load and start tasks from a file
        if file_name is not None:
            asyncio.create_task(self.load_tasks_file(file_name=file_name))

    async def stop(self):
        ''' Stop all running tasks '''
        if self.task_running:
            async with self._lock:
                self.stop_task = True
        if self.thread_running:
            self.stop_thread = True

    def _update(self, pattern:list, repeat=True, led_range=None, style=STYLE_ALL):
        ''' Push the formatted pattern to the light (ideal to call on a second core to prevent blocking) '''
        try:
            self.thread_running = True
            for dev_id in range(len(self.np_devices)):
                if pattern[dev_id] != []:
                    if isinstance(led_range, list) and isinstance(led_range[dev_id], tuple) and led_range[dev_id][0] <= self.np_devices[dev_id]['neo'].n and led_range[dev_id][1] <= self.np_devices[dev_id]['neo'].n:
                        write_range = led_range[dev_id]
                    else:
                        write_range = (0, self.np_devices[dev_id]['neo'].n)
                    i = write_range[0]
                    pattern_pos = 0
                    while i < write_range[1] and i < self.np_devices[dev_id]['neo'].n and pattern_pos >= 0:
                        self.np_devices[dev_id]['neo'][i] = pattern[dev_id][pattern_pos]
                        if style == STYLE_SEQ_LED:
                            self.np_devices[dev_id]['neo'].write()
                        if self.stop_thread:
                            self.stop_thread = False
                            self.thread_running = False
                            return
                        i += 1
                        if pattern_pos < len(pattern[dev_id]) - 1:
                            pattern_pos += 1
                        else:
                            if repeat:
                                pattern_pos = 0
                            else:
                                if style == STYLE_SEQ_DEV:
                                    self.np_devices[dev_id]['neo'].write()
                                pattern_pos = -1
                    if style == STYLE_SEQ_DEV:
                        self.np_devices[dev_id]['neo'].write()
            if STYLE_ALL:
                for dev_id in range(len(self.np_devices)):
                    if pattern[dev_id] != []:
                        self.np_devices[dev_id]['neo'].write()
        except Exception as e:
            self._logger.error(f"Error updating lights: {e}")
        finally:
            self.thread_running = False

    async def clear(self, devs=ALL_DEVS, style=STYLE_ALL, interval=0, clear_range=None, passthru=False):
        ''' Clear all lights
            devs - List of integers that match to the index of the light ([0] if there is only 1). None implies all
            style options:
                STYLE_ALL - clear all LED's at once on all devices (no interval)
                STYLE_SEQ_DEV - clear all LED's on each device sequentially (interval used between devices)
                STYLE_SEQ_LED - clera all LED's individually on each device (interval used between each LED)
            interval - milliseconds to wait between devices or LED's (depending on style)
            range (default None) - Range of LED's to clear.  Pass as a list, or as a range(x,x) object
                '''
        try:
            if not passthru:
                self._logger.info(f"Clearing lights {devs if devs is not None else 'All'}, style: {STYLES[str(style)]}, interval: {interval}, clear range: {clear_range}")
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
            write_pattern = []
            for dev_id in range(len(self.np_devices)):
                if devs is None or dev_id in devs:
                    write_pattern.append([(0,0,0)])
                else:
                    write_pattern.append([])
            start_new_thread(self._update, (write_pattern, True, clear_range, style))
            await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
        except Exception as e:
            self._logger.error(f'Error in clear async function: {e}')

    async def pattern(self, patterns:list, devs=ALL_DEVS, style=STYLE_ALL, start=0, fill_string=True, interval=0, clear=True, passthru=False):
        ''' Writes a light pattern to the led strip.
            devs - List of integers that match to the index of the light ([0] if there is only 1). None implies all
            patterns - dict of the following:
                color (required) - list of 3 integers 0-255 - [r, g, b]
                brightness (optional) - float for factoring colors - 0-1 - Each color will be multiplied by the factor (i.e. .25 for 25%)
                repeat (optional) - integer - number of LED's to repeat the color described above
                skip (optional) - integer - number of LED's to skip after the color described above
            style options:
                STYLE_ALL - clear all LED's at once on all devices (no interval)
                STYLE_SEQ_DEV - set all patterns on each device sequentially (interval used between devices)
                STYLE_SEQ_LED - set each pattern sequentially on each device (interval used between each pattern)
            interval - milliseconds to wait between devices or LED's (depending on style)
            start (default 0) - where on the string to start the pattern
            fill_string (default True) - repeats the pattern until the end of the string if True, othewise stops after 1 pattern
            clear - True / False - Clear uses the dev's passed using STYLE_ALL to reset the LED strips prior to starting the pattern
        '''
        try:
            if clear:
                self.clear(devs=devs, style=STYLE_ALL, interval=0, passthru=True)
            if not passthru:
                self._logger.info(f"Writing light pattern to devices {devs if devs is not None else 'ALL'}: {patterns}, style: {STYLES[str(style)]}, interval: {interval}")
            write_pattern = []
            for dev_id in range(len(self.np_devices)):
                write_pattern.append([])
                if devs is None or dev_id in devs:
                    for pattern in patterns:
                        for i in range(start, pattern.get('repeat', 0) + 1):
                            write_pattern[dev_id].append(tuple(pattern['color']) if 'brightness' not in pattern else tuple([round(x * pattern.get('brightness')) for x in pattern['color']]))
                        for i in range(start, pattern.get('skip', 0)):
                            write_pattern[dev_id].append((0,0,0))
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
            start_new_thread(self._update, (write_pattern, fill_string, None, style))
            await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
        except Exception as e:
            self._logger.error(f'Error in pattern async function: {e}')

    async def shift(self, shift=1, devs=ALL_DEVS, repeat=0, interval=0, loop=True, style=STYLE_ALL):
        ''' Move all LED's based on the shift given (either positive or negative)
            devs - List of integers that match to the index of the light ([0] if there is only 1). None implies all
            shift (default 1) - Moves all LED's plus or minus the number specified
            repeat (default 0) - Number of times to repeat the move
            interval (default 0) - Delay in ms between updates
            loop (default True) - Loop colors from end to beginning (or vise-versa). If False, will swipe off
        '''
        try:
            self._logger.info(f"Moving lights {devs if devs is not None else 'All'}, shift: {shift}, interval: {interval}, loop: {loop}")
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
            start_new_thread(self._shift_thread, (shift, devs, repeat, interval, loop, style))
            await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
        except Exception as e:
            self._logger.error(f'Error in Shift Async functon: {e}')

    def _shift_thread(self, shift:int, devs:list, repeat:int, interval:int, loop:bool, style:int):
        ''' Execute the shift on a thread to release blocking on the main thread'''
        try:
            self.thread_running = True
            while repeat >= 0:
                for dev_id in range(len(self.np_devices)):
                    if devs is None or dev_id in devs:
                        if shift > 0:
                            # get the last '[shift]' positions
                            temp_data = {}
                            for temp_pos in range(shift):
                                temp_data[str(self.np_devices[dev_id]['neo'].n - 1 - temp_pos)] = self.np_devices[dev_id]['neo'][self.np_devices[dev_id]['neo'].n - 1 - temp_pos] if loop else [0,0,0]
                            # shift the LED's
                            pos = self.np_devices[dev_id]['neo'].n - 1
                            while pos >= 0:
                                if pos - shift >= 0:
                                    self.np_devices[dev_id]['neo'][pos] = self.np_devices[dev_id]['neo'][pos - shift]
                                else:
                                    self.np_devices[dev_id]['neo'][pos] = temp_data[str(self.np_devices[dev_id]['neo'].n + (pos - shift))]
                                pos -= 1
                                if style == STYLE_SEQ_LED:
                                    self.np_devices[dev_id]['neo'].write()
                                    sleep_ms(interval)
                                if self.stop_thread:
                                    self.stop_thread = False
                                    self.thread_running = False
                                    return
                        else:
                            temp_data = {}
                            for temp_pos in range(abs(shift)):
                                temp_data[str(temp_pos)] = self.np_devices[dev_id]['neo'][temp_pos] if loop else [0,0,0]
                            pos = 0
                            while pos < self.np_devices[dev_id]['neo'].n:
                                if (pos + abs(shift)) < self.np_devices[dev_id]['neo'].n:
                                    self.np_devices[dev_id]['neo'][pos] = self.np_devices[dev_id]['neo'][pos + abs(shift)]
                                else:
                                    self.np_devices[dev_id]['neo'][pos] = temp_data[str(pos + abs(shift) - self.np_devices[dev_id]['neo'].n)]
                                pos += 1
                                if style == STYLE_SEQ_LED:
                                    self.np_devices[dev_id]['neo'].write()
                                    sleep_ms(interval)
                                if self.stop_thread:
                                    self.stop_thread = False
                                    self.thread_running = False
                                    return
                        if style == STYLE_SEQ_DEV:
                            self.np_devices[dev_id]['neo'].write()
                            sleep_ms(interval)
                if style == STYLE_ALL:
                    for dev_id in range(len(self.np_devices)):
                        if devs is None or dev_id in devs:
                            self.np_devices[dev_id]['neo'].write()
                repeat -= 1
        except Exception as e:
            self._logger.error(f'Error in Shift thread function: {e}')
        finally:
            self.thread_running = False

    async def random(self, devs=ALL_DEVS, brightness=1.0, pattern_length=None, colors=None, style=STYLE_ALL, interval=0, clear=True):
        ''' Fills the string with random colors
            devs - List of integers that match to the index of the light ([0] if there is only 1). None implies all
            brightness (default 1) - Specify the brightness value between 0.0 and 1.0 for all LED's (the random will include different brightness levels)
            style options:
                STYLE_ALL - clear all LED's at once on all devices (no interval)
                STYLE_SEQ_DEV - set all patterns on each device sequentially (interval used between devices)
                STYLE_SEQ_LED - set each pattern sequentially on each device (interval used between each pattern)
            interval - milliseconds to wait between devices or LED's (depending on style)
            colors (default None) - List of colors that the random should pick from (list of RGB tuples), None will randomize all values
        '''
        try:
            if clear:
                await self.clear(devs=devs, style=STYLE_ALL, interval=0, passthru=True)
            self._logger.info(f"Writing random light pattern to devices {devs if devs is not None else 'ALL'}, style: {STYLES[str(style)]}, interval: {interval}")
            write_pattern = []
            for dev_id in range(len(self.np_devices)):
                write_pattern.append([])
                if devs is None or dev_id in devs:
                    for i in (range(pattern_length) if pattern_length is not None else range(self.np_devices[dev_id]['neo'].n)):
                        if colors is not None:
                            write_pattern[dev_id].append(tuple([round(x * brightness for x in colors[randrange(0, len(colors)-1)])]))
                        else:
                            write_pattern[dev_id].append((round(randrange(0,255) * brightness), round(randrange(0,255) * brightness), round(randrange(0,255) * brightness)))
            while self.thread_running:
                await asyncio.sleep_ms(250)
            start_new_thread(self._update, (write_pattern, True, None, style))
            await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            while self.thread_running:
                await asyncio.sleep_ms(250)
        except Exception as e:
            self._logger.error(f'Error in random async function: {e}')

    async def kitt(self, patterns:list, devs=ALL_DEVS, interval=0, repeat=0, jump=1, clear=True, clear_end=True, style=STYLE_SEQ_DEV, forward_clear_pattern=(0,0,0), reverse_clear_pattern=(0,0,0)):
        ''' Does a knight rider kitt style swipe of a pattern from one end of the string to the other and back
            patterns - list of dict of the following:
                color (required) - list of 3 integers 0-255 - [r, g, b]
                brightness (optional) - float for factoring colors - 0-1 - Each color will be multiplied by the factor (i.e. .25 for 25%)
                repeat (optional) - integer - number of LED's to repeat the color described above
                skip (optional) - integer - number of LED's to skip after the color described above
            devs (default None) - List of integers that match to the index of the light ([0] if there is only 1). None implies all
            interval (default 0ms) - milliseconds to wait between jumps
            repeat (default 0) - number of times to repeat the kitt swipe (to end and back)
            jump (default 1) - number of pixels to move each time
            clear (default True) - clear the string before starting kitt
            clear_end (default True) - clear the string at the end of the kitt
            style (default STYLE_SEQ_DEV) - If STYLE_SEQ_DEV or STYLE_SEQ_LED runs "kitt" across all strands in sequence. STYLE_ALL runs each strand separate
            forward_clear_pattern (default(0,0,0)) - Sets the color to use to clearing the pattern going forward
            revertse_clear_pattern (default(0,0,0)) - Sets the color to use to clearing the pattern going in reverse
        '''
        try:
            if clear:
                await self.clear(devs=devs, style=STYLE_ALL, interval=0, passthru=True)
            self._logger.info(f"Starting kitt on {devs if devs is not None else 'ALL'}: {patterns}, style: {STYLES[str(style)]}, interval: {interval}, pattern: {patterns}, forward_clear: {forward_clear_pattern}, reverse_clear: {reverse_clear_pattern}")
            write_pattern = []
            for pattern in patterns:
                for i in range(pattern.get('repeat', 0) + 1):
                    write_pattern.append(tuple(pattern['color']) if 'brightness' not in pattern else tuple([round(x * pattern.get('brightness')) for x in pattern['color']]))
                for i in range(pattern.get('skip', 0)):
                    write_pattern.append((0,0,0))
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
            if style == STYLE_SEQ_DEV or style == STYLE_SEQ_LED:
                start_new_thread(self._kitt_thread_seq, (write_pattern, len(write_pattern), devs, interval, repeat, jump, forward_clear_pattern, reverse_clear_pattern))
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            else:
                start_new_thread(self._kitt_thread_individual, (write_pattern, len(write_pattern), devs, interval, repeat, jump, forward_clear_pattern, reverse_clear_pattern))
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS) # wait after starting the thread to make sure the running flag is set
            while self.thread_running:
                await asyncio.sleep_ms(NEOPX_ASYNC_WAIT_MS)
            if clear_end:
                await self.clear(devs=devs, style=STYLE_ALL, interval=0, passthru=True)
        except Exception as e:
            self._logger.error(f'Error in Kitt async function: {e}')


    def _kitt_thread_individual(self, pattern:list, pattern_size:int, devs:list, interval:int, repeat: int, jump:int, forward_clear_pattern=(0,0,0), reverse_clear_pattern=(0,0,0)):
        ''' Thread to run the "kitt" back and forth on all provided strands simultaneously.  Repeat will continue until all strands reach the repeat count '''
        self.thread_running = True
        devs = list(range(len(self.np_devices))) if devs is None else devs
        dev_list = [{'dev_id': devs[i], 'neo': self.np_devices[i]['neo'], 'prev_pos': -1, 'pos': 0, 'repeat': repeat, 'forward': True} for i in devs]
        def loop_continue(): # return False when all strands have reached their repeat counter
            if self.stop_thread:
                self.stop_thread = False
                self.thread_running = False
                return
            for dev in dev_list:
                if dev['repeat'] >= 0:
                    return True
            return False
        try:
            while loop_continue():
                for dev in dev_list:
                    # clear the previous pattern
                    for pixel in (pattern if dev['forward'] else reversed(pattern)):
                        if dev['prev_pos'] >= 0:
                            dev['neo'][dev['prev_pos']] = forward_clear_pattern if dev['forward'] else reverse_clear_pattern
                        dev['prev_pos'] = (dev['prev_pos'] + 1) if dev['forward'] else (dev['prev_pos'] - 1)
                    dev['prev_pos'] = dev['pos'] # save where we are about to start writing
                    # write the new pattern
                    for pixel in (pattern if dev['forward'] else reversed(pattern)):
                        dev['neo'][dev['pos']] = pixel
                        dev['pos'] = (dev['pos'] + 1) if dev['forward'] else (dev['pos'] - 1)
                    # update pos
                    dev['pos'] = (dev['pos'] - pattern_size + jump) if dev['forward'] else (dev['pos'] + pattern_size - jump)
                    # reverse if we reached the end
                    if dev['forward'] and dev['pos'] + pattern_size >= dev['neo'].n:
                        dev['forward'] = False
                    elif not dev['forward'] and dev['pos'] - pattern_size < 0:
                        dev['forward'] = True
                        dev['prev_pos'] = dev['pos'] - pattern_size # shift the previous point to the opposite side of pattern
                        dev['repeat'] -= 1
                # write the strings
                for dev in dev_list:
                    dev['neo'].write()
                    if self.stop_thread:
                        self.stop_thread = False
                        self.thread_running = False
                        return
                    sleep_ms(interval)
        except Exception as e:
            self._logger.error(f'Error in Kitt individual thread: {e}')
        self.thread_running = False

    def _kitt_thread_seq(self, pattern:list, pattern_size:int, devs:list, interval:int, repeat:int, jump:int, forward_clear_pattern=(0,0,0), reverse_clear_pattern=(0,0,0)):
        ''' Thread to run the "kitt" back and forth across all strands in the devs list in sequence '''
        self.thread_running = True
        devs = list(range(len(self.np_devices))) if devs is None else devs
        def forward_continue():
            if self.stop_thread:
                self.stop_thread = False
                self.thread_running = False
                return
            if devs.index(dev) == len(devs) - 1:
                if pos + pattern_size + jump >= self.np_devices[len(devs)-1]['neo'].n:
                    return False
            return True
        def reverse_continue():
            if self.stop_thread:
                self.stop_thread = False
                self.thread_running = False
                return
            if devs.index(dev) == 0:
                if pos - pattern_size - jump < 0:
                    return False
            return True
        try:
            while repeat >= 0:
                prev_pos = -1
                prev_dev = devs[0]
                pos = 0
                dev = devs[0]
                # start the move to the end
                self._logger.debug(f"Forward repeat {repeat}, dev: {dev}, pos: {pos}, devs: {devs}")
                while forward_continue():
                    # clear the previous pattern
                    if prev_pos >= 0:
                        for pixel in pattern:
                            if prev_pos >= self.np_devices[prev_dev]['neo'].n:
                                prev_dev = devs[devs.index(prev_dev) + 1]
                                prev_pos = 0
                        self.np_devices[prev_dev]['neo'][prev_pos] = forward_clear_pattern
                        prev_pos += 1
                    # write the new pattern
                    prev_dev = dev # save which string we started on
                    prev_pos = pos # save which pixel we started on
                    for pixel in pattern:
                        if pos >= self.np_devices[dev]['neo'].n:
                            dev = devs[devs.index(dev) + 1] # jump to the next strip
                            pos = 0
                        self.np_devices[dev]['neo'][pos] = pixel
                        pos += 1
                    # update pos
                    pos = pos  - pattern_size + jump
                    # write the strings
                    for dev_id in range(len(self.np_devices)):
                        if dev_id in devs:
                            self.np_devices[dev_id]['neo'].write()
                            if self.stop_thread:
                                self.stop_thread = False
                                self.thread_running = False
                                return
                            sleep_ms(interval)
                # move back to the beginning
                self._logger.debug(f"Reverse repeat {repeat}, dev: {dev}, pos: {pos}, devs: {devs}")
                while reverse_continue():
                    # clear the previous pattern
                    for pixel in reversed(pattern):
                        if prev_pos < 0:
                            prev_dev = devs[devs.index(prev_dev) - 1]
                            prev_pos = self.np_devices[prev_dev]['neo'].n - 1
                        self.np_devices[prev_dev]['neo'][prev_pos] = reverse_clear_pattern
                        prev_pos -= 1
                    # write the new pattern
                    prev_dev = dev # save which string we started on
                    prev_pos = pos # save which pixel we started on
                    for pixel in reversed(pattern):
                        if pos < 0:
                            dev = devs[devs.index(dev) - 1]
                            pos = self.np_devices[dev]['neo'].n - 1
                        self.np_devices[dev]['neo'][pos] = pixel
                        pos -= 1
                    # update pos
                    pos = pos - jump + pattern_size
                    if pos >= self.np_devices[dev]['neo'].n:
                        pos = pos - self.np_devices[dev]['neo'].n
                        dev = devs[devs.index(dev) + 1] # jump to the next strip
                    # write the strings
                    for dev_id in range(len(self.np_devices)):
                        if dev_id in devs:
                            self.np_devices[dev_id]['neo'].write()
                            if self.stop_thread:
                                self.stop_thread = False
                                self.thread_running = False
                                return
                            sleep_ms(interval)
                repeat -= 1
        except Exception as e:
            self._logger.error(f'Error in Kitt Seq Thread: {e}')
        finally:
            self.thread_running = False

    async def exec_tasks(self, repeat:int, tasks=list):
        ''' Execute a list of tasks and repeat:
            repeat - int - number of times to repeat the task list. None would be forever
            tasks - list - list of dict for the tasks to be run, format as:
                [
                    {
                        'name': (required) '[task_name(clear|pattern|shift|random|kitt)]',
                        'args': (optional) [list of args to pass to the function],
                        'kwargs': (optional) {dicyt of args to pass to the function},
                        'sleep': (optional) [int seconds to sleep after task is complete, default is 0]
                    }
                ] '''
        # check if a task or thread is running
        if self.task_running:
            self._logger.info("Starting task, but another task is running.  Sending stop.")
            await asyncio.gather(self.stop())
            timeout = time() + 10
            while self.task_running and time() < timeout:
                await asyncio.sleep_ms(100)
            if self.task_running:
                self._logger.error(f"Error running task.  Another task is already running and stop failed!")
                return
        if self.thread_running:
            self._logger.info("Starting task, but another thread is running.  Sending stop.")
            await asyncio.gather(self.stop())
            timeout = time() + 10
            while self.thread_running and time() < timeout:
                await asyncio.sleep_ms(100)
            if self.thread_running:
                self._logger.error("Error running task.  A thread is already running and stop failed!")
                return
        async with self._lock:
            self.task_running = True
        self._logger.info(f'Repeat: {repeat}, Exec tasks: {tasks}')
        while repeat is None or repeat >= 0:
            try:
                for task in tasks:
                    self._logger.debug(f"Running Task: {task}")
                    if self.stop_task:
                        async with self._lock:
                            self._logger.debug('STOP TASK')
                            self.task_running = False
                            self.stop_task = False
                        return
                    if task.get('name', None) in dir(self): # check if name is a function in the class
                        self._logger.debug(f"Running task {task.get('name')} with args/kwargs: {task.get('args', [])}/{task.get('kwargs',{})}")
                        func_obj = getattr(self, task.get('name')) # get the function pointer
                        await asyncio.gather(func_obj(*(task.get('args', None) if task.get('args', None) is not None else []), **(task.get('kwargs', None) if task.get('kwargs', None) is not None else {})))
                    if 'sleep' in task:
                        self._logger.debug(f"Task sleep for {task['sleep']} seconds...")
                        await asyncio.sleep(task['sleep'])
                    if repeat is not None:
                        repeat -= 1
            except Exception as e:
                self._logger.error(f'Error running task: {e}')
        async with self._lock:
            self.task_running = False

    async def load_tasks_file(self, file_name:str, run_tasks=True):
        ''' Load a json file with the task list from file and start the task '''
        try:
            self._logger.error(f'Loading tasks file {file_name}')
            with open(file_name, 'r', encoding='utf-8') as input_file:
                json_data = loads(input_file.read())
            if run_tasks:
                asyncio.create_task(self.exec_tasks(json_data))
            return json_data
        except Exception as e:
            self._logger.error(f'Error Loading tasks file {file_name}: {e}')
