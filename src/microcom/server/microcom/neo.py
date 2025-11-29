import asyncio
from random import randint
from json import dumps
from neopixel import NeoPixel
from machine import Pin # type: ignore # pylint: disable=E0401
from microcom.log_manager import logging_handler, INFO
from microcom.exceptions import MicrocomException
from microcom.async_lock import AsyncLock

class MicrocomNeoPixelDevice:
    ''' Class to represent a single string and hold relevant config and parameters '''
    def __init__(self, pin:int, count:int, rgbw:bool, timing800:bool, _logger:logging_handler, name:str|None=None, max_brightness:int=255, clear:bool=True):
        self.pin, self.count, self._logger, self.max_brightness, self._rgbw, self._timing800 = pin, count, _logger, max_brightness, rgbw, timing800
        self.name = name if name is not None else pin
        self.led_bytes = 4 if rgbw else 3
        self._logger.info(f"NeoPixel: Initializing pin {pin}, count {count}, rgbw {rgbw}, 800kHz: {timing800}, Max Brightness: {max_brightness}...")
        self.dev = NeoPixel(pin=Pin(pin),  # pylint: disable=E1123
                            n=count,
                            bpp=self.led_bytes,
                            timing=1 if timing800 else 0 # type: ignore
                            )
        if clear:
            self.clear()

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, pin={self.pin}, count={self.count}, rgbw={self._rgbw}, timing800={self._timing800}, max_brightness={self.max_brightness})"

    def __str__(self):
        ''' return a json dump of the device '''
        return dumps(self.json())

    def json(self) -> dict|list:
        ''' return a json representation of the object '''
        return {'name': self.name, 'pin': self.pin, 'count': self.count, 'rgbw': self._rgbw, 'timing800': self._timing800, 'max_brightness': self.max_brightness}

    def __del__(self):
        try:
            self.clear()
        except: #pylint: disable=W0702
            pass

    def clear(self):
        ''' Clear a string '''
        self._logger.debug(f"NeoPixel({self.pin}): Clear")
        self.dev.fill((0,0,0))
        self.dev.write()

    def write(self):
        ''' Write the current buffer to the string '''
        self.dev.write()

    def pattern(self, pattern:tuple[tuple[tuple[int],int]], fill:bool=True, write:bool=True, reverse:bool=False, carry_over:bytes=b'', start:int|None=None) -> bytes:
        ''' Write a pattern to the light string. Pattern must be a list of tuples objects:
            start (int|None) - If None, defaults to the beginning or end of the string (end if reverse is True)
            pattern (tuple) (
                (
                    tuple: RGB(W) color to apply,
                    repeat: number of times to repeat the color
                )
            )
            Returns -> any bytes from the pattern that are left over (for other strings)
            '''
        # first convert the pattern to a string of bytes to write to the buffer
        pattern_list = [self.pattern_to_bytes(*x) for x in pattern]
        if reverse:
            pattern_list.reverse()
        pattern_bytes = b''.join(pattern_list)
        pattern_leds = len(pattern_bytes) // 3
        self._logger.debug(f"NeoPixel({self.pin}): pattern: {pattern}, fill: {fill}, write: {write}, reverse: {reverse}, carry_over: {carry_over}, "
                           f"pattern bytes: {pattern_bytes}, start: {start}")
        if start is None:
            led_pos = 0 if not reverse else self.count - 1
        else:
            led_pos = start
        left_over_bytes = b''
        while (led_pos < self.count) if not reverse else (led_pos >= 0):
            # write the pattern bytes to the buffer
            if not reverse:
                if len(carry_over) > 0:
                    # fill in any part of the pattern that was a carry over
                    new_led_pos = led_pos + (len(carry_over) // self.led_bytes)
                    carry_over = self.write_led(led_pos, carry_over)
                    led_pos = new_led_pos
                left_over_bytes = self.write_led(led_pos, pattern_bytes)
            else:
                if len(carry_over) > 0:
                    # fill in any part of the pattern that was a carry over
                    new_led_pos = led_pos-pattern_leds
                    carry_over = self.write_led_reverse(led_pos, carry_over)
                    led_pos = new_led_pos
                left_over_bytes = self.write_led_reverse(led_pos, pattern_bytes)
            if not fill:
                break
            led_pos = led_pos + pattern_leds if not reverse else led_pos - pattern_leds
        if write:
            self.dev.write()
        self._logger.debug(f"NeoPixel({self.pin}): pattern left over bytes: {left_over_bytes}")
        return left_over_bytes # return any bytes that were left over from the pattern

    def shift(self, count:int, write:bool=True, carry_over:bytes|None=None) -> bytes:
        ''' Shift all the lights either forward (positive count) or backwards (negative count) in the string'''
        if count >= 0:
            # get the bytes that are going to be overwritten at the end
            shift = bytes(self.dev.buf[len(self.dev.buf)-count*self.led_bytes:]) # type:ignore # pylint: disable=E1101
            self._logger.debug(f"NeoPixel({self.pin}): shift: {count}, shift bytes: {shift}")
            # shuft the buffer backwardsd by the number of led's
            self.dev.buf[len(shift):] = self.dev.buf[0:len(self.dev.buf)-len(shift)] # type:ignore # pylint: disable=E1101
            # add the bytes that were overwritten to the start of the string
            self.dev.buf[0:len(shift)] = shift if carry_over is None else carry_over # type:ignore # pylint: disable=E1101
        else:
            # negative count
            count = abs(count)
            # get the bytes that are going to be overwritten at the beginning
            shift = bytes(self.dev.buf[0:count*self.led_bytes]) # type:ignore # pylint: disable=E1101
            self._logger.debug(f"NeoPixel({self.pin}): shift: -{count}, shift bytes: {shift}")
            # shift the buffer forward by the number of led's
            self.dev.buf[0:len(self.dev.buf)-len(shift)] = self.dev.buf[len(shift):] # type:ignore # pylint: disable=E1101
            # add the bytes that were overwritten to the end of the string
            self.dev.buf[len(self.dev.buf)-len(shift):] = shift if carry_over is None else carry_over # type:ignore # pylint: disable=E1101
        if write:
            self.dev.write()
        return shift

    def write_bytes(self, data:bytes, reverse:bool=False, write:bool=False):
        ''' Write the specified bytes to the device '''
        if reverse:
            self.dev.buf[0-len(data):] = data # type:ignore # pylint: disable=E1101
        else:
            self.dev.buf[0:len(data)] = data # type:ignore # pylint: disable=E1101
        if write:
            self.dev.write()

    def random(self, write:bool=True, max_brightness:int=255):
        ''' Sets all LED's to random values '''
        # set the max range
        max_value = self.max_brightness if self.max_brightness < max_brightness else max_brightness # use the passed max if lower than string max
        self._logger.debug(f"NeoPixel({self.pin}): random, max value: {max_value}")
        buf_pos = 0
        while buf_pos < len(self.dev.buf): # type: ignore # pylint: disable=E1101
            self.dev.buf[buf_pos:buf_pos+self.led_bytes] = b''.join([randint(0, max_value).to_bytes(1,'big') for _ in range(self.led_bytes)]) # type:ignore # pylint: disable=E1101
            buf_pos += self.led_bytes
        if write:
            self.dev.write()

    def pattern_to_bytes(self, color:tuple[int], repeat:int) -> bytes:
        ''' Convert the color tuple into a bytes that can be inserted in the buffer, verify the brightness isn't exceeded '''
        scratch = list(color)
        for x in range(len(scratch)): #pylint: disable=C0200
            if scratch[x] > self.max_brightness:
                scratch[x] = self.max_brightness
        # swap byte order to GRB to match WS2811/2812 style
        data = [scratch[1], scratch[0], scratch[2]] if len(scratch) == 3 else [scratch[1], scratch[0], scratch[2], scratch[3]]
        return b''.join([x.to_bytes(1, 'big') for x in data]) * repeat

    def tuples_to_bytes(self, colors:tuple[tuple[int],]):
        ''' convert a tuple of color tupes to bytes with the brightness max applied '''
        color_bytes = b''
        for color in colors:
            color_bytes += self.pattern_to_bytes(color, 1)
        return color_bytes

    def write_led(self, pos, data:bytes) -> bytes:
        ''' write a list of bytes starting at a specific LED position in the string. Return bytes that went past the string. NOTE: Does not scale the max brightness! '''
        if len(data) % self.led_bytes != 0:
            raise ValueError(f"Require {self.led_bytes} per led, got {data}")
        byte_pos = pos * self.led_bytes
        if len(data) > len(self.dev.buf) - byte_pos: # type: ignore # pylint: disable=E1101
            # if we have a remainder, write the partial and return the rest
            self.dev.buf[pos*self.led_bytes:self.count*self.led_bytes] = data[0:len(self.dev.buf) - byte_pos] # type: ignore # pylint: disable=E1101
            return data[len(self.dev.buf) - byte_pos:] # type: ignore # pylint: disable=E1101
        # otherwise write the full data
        self.dev.buf[byte_pos:byte_pos+len(data)] = data # type: ignore # pylint: disable=E1101
        return b''

    def write_led_reverse(self, pos, data:bytes) -> bytes:
        ''' write a list of bytes starting at a specific LED position in reverse. '''
        if len(data) % self.led_bytes != 0:
            raise ValueError(f"Require {self.led_bytes} per led, got {data}")
        byte_pos = ((pos+1) * self.led_bytes) - len(data)
        if byte_pos < 0:
            # if we ahve a remainder, write the partial and return the rest
            self.dev.buf[0:(pos+1)*self.led_bytes] = data[abs(byte_pos):] # type: ignore # pylint: disable=E1101
            return data[0:abs(byte_pos)]
        # otherwise write the full data
        self.dev.buf[byte_pos:byte_pos+len(data)] = data # type: ignore # pylint: disable=E1101
        return b''


class MicrocomNeoPixelGroup:
    ''' Class to represent a group of Neopixel devices that will be controlled together '''
    def __init__(self, devices:list[dict], log_level:int=INFO):
        ''' Each string of devices requires the following: 
            {
                pin [required] (int): GPIO pin number,
                count [required] (int): number of neopixel devices in the string,
                rgbw [optional] (bool): True for Neopixel with 4 values (RGB + W), False for standard 3 value (RGB),
                timing800 [optional] (bool): True for 800kHz, False for 400kHz,
                max_brightness [optional] (int): Percent of max brightness to limit the LED's
            }'''
        try:
            self._write_lock, self._program_lock = AsyncLock(), AsyncLock()
            self._write_request, self._program_end_request = False, False
            self._logger = logging_handler(log_level)
            self._devices = []
            self._logger.debug(f"NEOPIXEL_GROUP: Initalizing: {devices}")
            for device in devices:
                self._devices.append(MicrocomNeoPixelDevice(pin=device['pin'],
                                                            count=device['count'],
                                                            rgbw=device.get('rgbw', False),
                                                            timing800=device.get('timing800', True),
                                                            _logger=self._logger,
                                                            max_brightness=device.get('max_brightness', 100)))
        except Exception as e:
            error = f"NEOPIXEL Unable to initialize, Error {e.__class__.__name__}: {e}"
            self._logger.error(error)
            raise MicrocomException(error) from e

    def __repr__(self):
        return f"{self.__class__.__name__}(devices={self._devices})"

    def __str__(self):
        ''' Return a json dumps of the device info '''
        return dumps(self.json())

    def json(self) -> dict|list:
        ''' return a json representation of the device group '''
        return {'devices': [x.json() for x in self._devices], 'log_level': self._logger.console_level}

    def __del__(self):
        try:
            self._logger.info(f"NEOPIXEL - terminating devices: {self._devices}...")
            _ = asyncio.wait_for(self.clear(), 3)
            del self._devices
        except: #pylint: disable=W0702
            pass

    async def pattern(self, pattern:tuple[tuple[tuple[int,int,int],int]], devices:list|bool=True, start:int=0, reverse:bool=False, fill:bool=True):
        ''' Apply a pattern to one or more devices
                devices (list|bool) - Either a list of devices (by number matching the order created), or True for all
                pattern (tuple) - (
                    rgb (tuple) - rgb value to apply, for rgbw devices, add a 4th value to the list
                    repeat (int) - number of times to repeat the light value
                )
                start (int) - which light to start at
                reverse (bool) - Apply the pattern in reverse starting at the end
                fill (bool) - Repeat the pattern until the string is full
        '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            await self._pattern_write(pattern=pattern, devices=devices, start=start, reverse=reverse, fill=fill)

    async def _pattern_write(self, pattern:tuple[tuple[tuple[int,int,int],int]], devices:list|bool=True, start:int=0, reverse:bool=False, fill:bool=True, write:bool=True):
        ''' Async task to write a pattern without requesting a lock - intended to be used by other functions that already HAVE a lock '''
        if isinstance(devices, bool):
            devices = list(range(len(self._devices)))
        # write the pattern to the string buffers, but wait until all are updated to write
        self._logger.debug(f"NEOPIXEL_GROUP: Pattern on devices: {devices}, pattern: {pattern}, start: {start}, reverse: {reverse}, fill: {fill}")
        left_over = b''
        if reverse:
            devices.reverse()
        string_list_pos = 0 # maintain what the current start POS for a device is in the list
        for x in devices:
            self._logger.debug(f"NEOPIXEL_GROUP: Pattern on device pin {self._devices[x].pin}, left_over: {left_over}")
            if start < string_list_pos + self._devices[x].count:
                left_over = self._devices[x].pattern(pattern, carry_over=left_over, write=False, reverse=reverse, fill=fill, start=start)
            string_list_pos += self._devices[x].count
        if write:
            for x in devices:
                self._devices[x].write()

    async def clear(self, devices:list|bool=True):
        ''' Clear all devices '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            if isinstance(devices, bool):
                devices = list(range(len(self._devices)))
            self._logger.debug(f"NEOPIXEL_GROUP: clearing on {devices}")
            for x in devices:
                self._devices[x].clear()

    async def write(self, devices:list|bool=True):
        ''' Write the devices buffer to the strings '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            if isinstance(devices, bool):
                devices = list(range(len(self._devices)))
            self._logger.debug(f"NEOPIXEL_GROUP: writing on {devices}")
            for x in devices:
                self._devices[x].write()

    async def random(self, devices:list|bool=True, max_brightness:int=255):
        ''' Set all LED's to a random value '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            if isinstance(devices, bool):
                devices = list(range(len(self._devices)))
            self._logger.debug(f"NEOPIXEL_GROUP: random on {devices}, max_brightness: {max_brightness}")
            for x in devices:
                self._devices[x].random(max_brightness=max_brightness)

    async def shift(self, count:int=1, devices:list|bool=True, repeat:int=1, clear_end:bool=False, interval:int=250, fill_bytes:bytes|None=None):
        ''' Shift the entire pattern forward or backwards the amount specified.  If repeat, the shift is repeated either the number 
            of times specified or forever (-1) '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            if isinstance(devices, bool):
                devices = list(range(len(self._devices)))
            self._logger.debug(f"NEOPIXEL_GROUP: shift on {devices}, count: {count}, repeat: {repeat}, clear_end: {clear_end}, interval: {interval}ms")
            while repeat > 0:
                carry_over = None
                # reverse the list if negative
                device_list = range(len(self._devices)) if count > 0 else range(len(self._devices) - 1, -1, -1)
                for device_id in device_list:
                    # loop through each string, take the bytes we are carrying over to the next string in the list
                    carry_over = self._devices[device_id].shift(count=count, write=False, carry_over=carry_over)
                # write the data carried over from the last string to the first
                self._devices[0].write_bytes(carry_over if fill_bytes is None else fill_bytes, reverse=count < 0)
                # write all the devices
                for x in devices:
                    self._devices[x].write()
                repeat -= 1
                await asyncio.sleep(interval / 1000.0)

        if clear_end:
            await self.clear()

    async def fade(self, pattern:tuple[tuple[tuple[int,int,int],int]], devices:list|bool=True, jump:int=1, delay_ms:int=0, color_pause_ms:int=0, repeat:int=2, clear_end:bool=False):
        ''' Fade entire group of strings. The max value is determined based on the lowest brightness of all the devices.
         The fade will move from one color to the next in the pattern by adding/subtracting from the R/G/B values.
          'jump' is used to factor the transition. 1 will allow a step for every different in value, 2 will allow 2 steps '''
        # loop through the devices and get the lowest brightness value
        if isinstance(devices, bool):
            devices = list(range(len(self._devices)))
        max_val = 255
        for x in devices:
            if self._devices[x].max_brightness < max_val:
                max_val = self._devices[x].max_brightness

        # adjust the patterns so they do not exceed the max brightness
        temp = []
        for color in pattern:
            temp.append(tuple([tuple([c if c < max_val else max_val for c in color[0]]), 1]))
        pattern = tuple(temp)
        self._logger.debug(f"NEOPIXEL GROUP: Updated fade pattern: {pattern}")

        self._logger.debug(f"NEOPIXEL GROUP: fade on {devices}, max_val: {max_val} jump: {jump}, delay_ms: {delay_ms}, color_pause_ms: {color_pause_ms}, repeat: {repeat}, clear_end: {clear_end}, pattern: {pattern}")
        if len(pattern) < 2:
            raise ValueError(f"NEOPIXEL GROUP fade function requires at least two patterns for the fade function, got {pattern}")

        # start the strings on the 1st pattern
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            await self._pattern_write(devices=devices, pattern=(pattern[0],))
            while (repeat > 0 if repeat != -1 else True):
                for x in range(len(pattern)): # pylint: disable=C0200
                    # repeat for each pattern
                    if x < len(pattern) - 1:
                        start_color = pattern[x]
                        end_color = pattern[x+1]
                    else:
                        start_color = pattern[x]
                        end_color = pattern[0]
                    # calculate diffs between the two colors
                    r_diff = ((start_color[0][0] if start_color[0][0] < max_val else max_val) - (end_color[0][0] if end_color[0][0] < max_val else max_val)) * -1
                    g_diff = ((start_color[0][1] if start_color[0][1] < max_val else max_val) - (end_color[0][1] if end_color[0][1] < max_val else max_val)) * -1
                    b_diff = ((start_color[0][2] if start_color[0][2] < max_val else max_val) - (end_color[0][2] if end_color[0][2] < max_val else max_val)) * -1
                    steps = (max(abs(r_diff), abs(g_diff), abs(b_diff)) // jump) - 1
                    r_val, g_val, b_val = start_color[0][0] if start_color[0][0] < max_val else max_val, start_color[0][1] if start_color[0][1] < max_val else max_val, start_color[0][2] if start_color[0][2] < max_val else max_val
                    r_step, g_step, b_step = r_diff / steps, g_diff / steps, b_diff / steps
                    self._logger.debug(f"NEOPIXEL GROUP: fade on {devices}, pattern {start_color} to {end_color}, diffs: {(r_diff, g_diff, b_diff)}, steps: {(r_step, g_step, b_step)}")
                    for _ in range(steps):
                        # loop through the steps and increment the r/g/b_val for each loop
                        r_val, g_val, b_val = r_val + r_step, g_val + g_step, b_val + b_step
                        await self._pattern_write(devices=devices, pattern=(((int(r_val), int(g_val), int(b_val)), 1),))
                        await asyncio.sleep_ms(delay_ms)
                        if self._write_request: # if there is a new write request, stop here, clearing is up to the new write request if needed
                            return
                    # for the last, just set to the end color before looping back around
                    await self._pattern_write(devices=devices, pattern=(end_color,))
                    await asyncio.sleep_ms(color_pause_ms)
                if repeat != -1:
                    repeat -= 1

        if clear_end:
            self.clear()

    async def kitt(self, pattern:tuple[tuple[tuple[int,int,int],int]], fwd_pattern:tuple[tuple[int,int,int],int], rev_pattern:tuple[tuple[int,int,int],int], devices:list|bool=True,
                   interval:int=250, end_swipe_interval:int=250, repeat:int=1, jump:int=1, clear_start:bool=True, clear_end:bool=False):
        ''' Run the 'kitt' swipe from left to right.  The pattern is the kitt object that is moving, fwd pattern is the fill to the left, rev_pattern is fill to the right
         devices (list|bool) - list of device indexes, or True for all devices
         interval (int) - ms between jumps
         repeat (int) - number of times to repeat a swipe to the right and back (-1 )
         jump (int) - number of pixels to jump at each interval
         clear_start (bool) - if True, clears the strings first, if False, the kitt swipe will overwrite as it goes
         clear_end (bool) - if True, clears the strings at the end of the repeat count
           '''
        self._write_request = True # Request a write - notifies any long running job to stop
        async with self._write_lock: # create a lock when previous jobs complete
            self._write_request = False # clear the write request
            if isinstance(devices, bool):
                devices = list(range(len(self._devices)))

            if clear_start:
                for x in devices:
                    self._devices[x].clear()

            self._logger.debug(f"NEOPIXEL_GROUP: kitt on {devices}, jump {jump}, interval: {interval}ms, end swipe interval: {end_swipe_interval}ms, repeat: {repeat}, " \
                            f"clear_start: {clear_start}, clear_end: {clear_end}, pattern: {pattern}, fwd_pattern: {fwd_pattern}, rev_pattern: {rev_pattern}")
            await asyncio.sleep(.01)

            # get total length of kitt pattern
            pattern_length = 0
            for pattern_block in pattern:
                pattern_length += pattern_block[1] # get the repeat count from the pattern

            # get total length of strings
            total_length = 0
            for x in devices:
                total_length += self._devices[x].count

            while repeat > 0:
                # write the start pattern
                await asyncio.sleep(end_swipe_interval / 1000.0)
                await self._pattern_write(pattern=pattern, devices=devices, start=0, fill=False, write=False)
                await self._pattern_write(pattern=((rev_pattern[0], 1),), devices=devices, start=pattern_length, fill=True, write=True) # write on second
                if self._write_request: # if there is a new write request, stop here, clearing is up to the new write request if needed
                    return

                # loop until we get to the end of the string
                current_pos = 0
                end_pos = total_length - pattern_length
                while current_pos + pattern_length - 1 <= end_pos:
                    # write the forward fill pattern, and pattern (skip the rev since it is already there)
                    await asyncio.sleep(interval / 1000.0)
                    await self._pattern_write(pattern=((fwd_pattern[0], pattern_length),), devices=devices, start=current_pos, fill=False, write=False)
                    await self._pattern_write(pattern=pattern, devices=devices, start=current_pos + jump, fill=False, write=True)
                    current_pos += jump
                    if self._write_request: # if there is a new write request, stop here, clearing is up to the new write request if needed
                        return

                # Jump to the end of the string (incase we don't hit the end in the jump loop)
                await asyncio.sleep(interval / 1000.0)
                await self._pattern_write(pattern=((fwd_pattern[0], 1),), devices=devices, start=total_length - pattern_length - 1, fill=True, reverse=False, write=False)
                await self._pattern_write(pattern=pattern, devices=devices, start=total_length - pattern_length, reverse=False, fill=False, write=True) # write on second
                if self._write_request: # if there is a new write request, stop here, clearing is up to the new write request if needed
                    return

                # loop until we get to the start of the string
                current_pos = total_length - pattern_length - 1
                end_pos = 0
                while current_pos - pattern_length >= end_pos:
                    # write the forward fill pattern, and pattern (skip the rev since it is already there)
                    await asyncio.sleep(interval / 1000.0)
                    await self._pattern_write(pattern=pattern, devices=devices, start=current_pos - pattern_length, reverse=False, fill=False, write=False)
                    await self._pattern_write(pattern=((rev_pattern[0], 1),), devices=devices, start=current_pos, fill=True, reverse=False, write=True) # write on second
                    current_pos -= jump
                    if self._write_request: # if there is a new write request, stop here, clearing is up to the new write request if needed
                        return
                await asyncio.sleep(end_swipe_interval / 1000.0)

                repeat -= 1

        if clear_end:
            await self.clear()

    async def run_program(self, program_list:list):
        ''' Run a series of commands one after the next, program_list format:
         program_list (list): [
            {
                "[function name, i.e. kitt, fade, pattern, etc]": {[args to pass to the specified function]},
                "delay": 1  ## time to wait AFTER the function before moving to the next
            }
         ]
           '''
        self._program_end_request = True
        async with self._program_lock:
            self._program_end_request = False
            for item in program_list:
                if isinstance(item, dict):
                    self._logger.info(f"NEOPIXEL_GROUP: Program item: {item}")
                    for key, value in item.items():
                        if key in dir(self) and key != 'delay':
                            # call the function and pass the parameters, wait for it to complete
                            self._logger.info(f"NEOPIXEL_GROUP: Program function: {key}, parameters: {value}")
                            await getattr(self, key)(**value)
                            if self._program_end_request:
                                # if we have a end program request, end the program
                                return
                        elif key != "delay":
                            self._logger.info(f"NEOPIXEL_GROUP: run program cannot execute function '{key}'. Not available.")
                    self._logger.info(f"NEOPIXEL_GROUP: Waiting {item.get('delay',0)} seconds before executing next function...")
                    await asyncio.sleep(item.get('delay', 0))
                else:
                    self._logger.error(f"NEOPIXEL_GROUP: run program cannot execute Expecting a dict containing {{[function]: {{[kwargs]}} }}, got {item}.")

    async def end_program(self, clear:bool=False):
        ''' Terminate an currently running program '''
        self._program_end_request = True
        async with self._program_lock:
            self._program_end_request = False

        if clear:
            await self.clear()
