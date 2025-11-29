'''
Display handler for OLED or LCD
'''
# pylint: disable=W0223
import os
from gc import collect
from microcom.i2c import MicrocomI2CBus
from microcom.log_manager import logging_handler
from microcom.exceptions import MicrocomDisplayException, MicrocomFontException


def text_justify(text:str, length:int, justify:str='l'):
    ''' Return a string with justification applied '''
    if len(text) > length:
        raise MicrocomDisplayException(f"Text line '{text}' longer than max length {length}")
    pad = length - len(text)
    if justify == 'r':
        return list(''.join(' ' * (pad)) + text)
    elif justify == 'c':
        left_pad = pad // 2
        return list(''.join(' ' * left_pad) + text  + ''.join(' ' * (length - len(text) - left_pad)))
    else: # justify == 'l'
        return list(text[0:length] + ''.join(' ' * pad))


class MicrocomDisplayI2C:
    ''' Class to represent a LCD or OLED display '''
    def __init__(self, _logger:logging_handler, i2c:MicrocomI2CBus, address:int):
        self._logger, self._i2c, self._address = _logger, i2c, address
        self._disp = None
        self._lines = self._cols = 0
        self.text_buffer = []
        self.display_type, self.display_bus, self.res_x, self.res_y = 'n/a', 'n/a', 0, 0

    def __del__(self):
        ''' Delete any obkects '''
        collect()

    def clear_display(self, send:bool=True):
        ''' Clear the display '''
        for line in self.text_buffer:
            line = [' '] * len(line)
        if send:
            self.write_display()

    def write_text(self, text:str, line:int=0, clear:bool=True, wrap:bool=True, justify:str='l', send:bool=True):
        ''' Write text to the display, justify can be 'l', 'c' or 'r' for left, center or right. Text can be multiline separated by '\n' '''
        if justify not in ['l', 'c', 'r']:
            raise MicrocomDisplayException(f"Unable to write text to display, justify '{justify}' not supported. Must be 'l', 'c', or 'r'")
        if clear:
            self.clear_display(send=False)
        self._logger.debug(f"Writing display line {text}, starting line: {line}, clear: {clear}, wrap: {wrap}, justify: {justify}, send: {send}")
        # check for a blank line
        if text == '':
            self.text_buffer[line] = [' '] * len(self.text_buffer[line])
        else:
            for text_line in text.split('\n'):
                if line + 1 > len(self.text_buffer):
                    self._logger.error(f"Unable to write text line to display. Not enough display lines. Total lines: {len(self.text_buffer)}, current line: {line}. Text: {text}")
                    raise MicrocomDisplayException("Unable to write text to display, no text lines free.")
                # move text into the boffer
                self.text_buffer[line] = text_justify(text_line[0:len(self.text_buffer[line])], length=len(self.text_buffer[line]), justify=justify)
                # if wordwrap is enabled, wrap and text beyond the line to the next
                if wrap and len(text_line) > len(self.text_buffer[line]):
                    wrap_text = text_line[len(self.text_buffer[line]):len(self.text_buffer[line])]
                    line = line + 1
                    if line + 2 > len(self.text_buffer):
                        self._logger.error(f"Unable to write text line to display (wrapped text). Not enough display lines. Total lines: {len(self.text_buffer)}, current line: {line}. Text: {wrap_text}")
                        raise MicrocomDisplayException("Unable to write text to display (wrapped text), no text lines free.")
                    while wrap_text != '':
                        self.text_buffer[line] = text_justify(wrap_text[0:len(self.text_buffer[line])], length=len(self.text_buffer[line]), justify=justify)
                        wrap_text = text_line[len(self.text_buffer[line]):len(self.text_buffer[line+1])]
                        line = line + 1
        if send:
            self.write_display()

    def write_display(self):
        ''' Override on a per class basis '''
        raise NotImplementedError('_print_text must be overriden by inheritting class')


class MicrocomDisplayI2C_PFC8574(MicrocomDisplayI2C):
    ''' Represent a LCD with 1 or more rows of text '''
    def __init__(self, _logger:logging_handler, lines:int, cols:int, i2c:MicrocomI2CBus, address:int):
        super().__init__(_logger, i2c, address)
        from libs.i2c_lcd import I2cLcd # pylint: disable=C0415
        self._disp = I2cLcd(i2c._bus, address, lines, cols)
        self._buffer = [[' ']*cols]*lines
        self.display_type, self.display_bus, self.res_x, self.res_y = 'pcf8574', 'i2c', cols, lines


class MicrocomDisplayI2C_FrameBuf(MicrocomDisplayI2C):
    ''' Represent a framebuffer style display that uses external fonts '''
    def __init__(self, _logger: logging_handler, i2c: MicrocomI2CBus, address: int, font_path:str='/fonts'):
        super().__init__(_logger, i2c, address)
        self.font_path = font_path
        self.fonts = {}
        self.loaded_fonts = {}
        self._framebuf = __import__('framebuf')

    def __del__(self):
        ''' Delete any objects '''
        for font in self.loaded_fonts:
            self.unload_font(*font.split('_'))
        del self._framebuf
        super().__del__()

    def font_list(self) -> dict:
        ''' Return a list of fonts that are available 
            format:
                {
                    '[fontname]': {
                        '[style]': [...list of sizes available...]
                    }
                }'''
        if not self.fonts:
            # if we don't have a font list, build the list
            files = os.listdir(self.font_path)
            for file in files:
                # fonts must end with .py generated by micropython-font-to-py: https://github.com/peterhinch/micropython-font-to-py/tree/master
                if file.endswith('.py'):
                    # font file must be [fontname]_[style]_[size].py
                    file_parts = file.split('.')[0].split('_')
                    if len(file_parts) == 3:
                        if file_parts[0] not in self.fonts:
                            self.fonts[file_parts[0]] = {file_parts[1]: [file_parts[2]]}
                        else:
                            if file_parts[1] not in self.fonts[file_parts[0]]:
                                self.fonts[file_parts[0]][file_parts[1]] = [file_parts[2]]
                            else:
                                self.fonts[file_parts[0]][file_parts[1]].append(file_parts[2])
        return self.fonts

    def load_font(self, font:str, style:str, size:str|int):
        ''' Load a specified font, style and size into memory for use by the display '''
        font_list = self.font_list()
        if font not in font_list or style not in font_list[font] or str(size) not in font_list[font][style]:
            raise MicrocomFontException(f"Cannot load {font}_{style}_{size}. Font not available.")
        if f"{font}_{style}_{size}" not in self.loaded_fonts:
            self._logger.debug(f"Loading font {font}_{style}_{size}...")
            try:
                # __import__ loads the font as fonts.[..name..], use getattr to get the actual font object instead of "fonts"
                self.loaded_fonts[f"{font}_{style}_{size}"] = getattr(__import__(f"{self.font_path.replace('/', '.').lstrip('.')}.{font}_{style}_{size}"), f"{font}_{style}_{size}")
            except ImportError as e:
                self._logger.error(f"Error loading font {font} {style} {size}, {e.__class__.__name__}: {e}")

    def unload_font(self, font:str, style:str, size:str|int):
        ''' Unload a font from memory '''
        if f"{font}_{style}_{size}" not in self.loaded_fonts:
            raise MicrocomFontException(f"Cannot unload {font}_{style}_{size}. Font not loaded.")
        self._logger.debug(f"Unloading font {font}_{style}_{size}...")
        del self.loaded_fonts[f"{font}_{style}_{size}"]
        collect()

    def font_height(self, font:str, style:str, size:str|int):
        ''' Return the font height '''
        if f"{font}_{style}_{size}" not in self.loaded_fonts:
            raise MicrocomFontException(f"Cannot read font height {font}_{style}_{size}. Font not loaded.")
        return self.loaded_fonts[f"{font}_{style}_{size}"].height()

    def font_width(self, font:str, style:str, size:str|int):
        ''' Return the font height '''
        if f"{font}_{style}_{size}" not in self.loaded_fonts:
            raise MicrocomFontException(f"Cannot read font width {font}_{style}_{size}. Font not loaded.")
        return self.loaded_fonts[f"{font}_{style}_{size}"].max_width()

    def get_char_buffer(self, char:str|int, font:str, style:str, size:str|int) -> tuple[bytearray, int, int]:
        ''' get the byte array for the character, height and width '''
        if f"{font}_{style}_{size}" not in self.loaded_fonts:
            raise MicrocomFontException(f"Cannot read font width {font}_{style}_{size}. Font not loaded.")
        glyph, height, width = self.loaded_fonts[f"{font}_{style}_{size}"].get_ch(str(char))
        return (bytearray(glyph), height, width)


class MicrocomDisplayI2C_SSD1306(MicrocomDisplayI2C_FrameBuf):
    ''' Represent an OLED '''
    def __init__(self, _logger: logging_handler, i2c:MicrocomI2CBus, config:dict):
        super().__init__(_logger, i2c, config['address'])
        self._ssd1306_i2c = __import__('libs/ssd1306')
        self._disp = self._ssd1306_i2c.SSD1306_I2C(config['width'], config['height'], i2c._bus, config['address'])
        self._width, self._height, self._rows, self.invert = config['width'], config['height'], [], False # not inverted
        # add direct access to contrast
        self.contrast = self._disp.contrast
        self.contrast(config.get('contrast', 255))
        self.display_type, self.display_bus, self.res_x, self.res_y = "ssd1306", "i2c", config['width'], config['height']
        self._logger.info(f"SSD1306 Display on {i2c} address {config['address']} initialized")
        for row_config in config.get('row_configs', []):
            self.add_text_row_config(**row_config)
        for line in config.get('lines', []):
            self.write_text(**line)

    def __del__(self):
        ''' Delete any active objects '''
        del self._disp
        del self._ssd1306_i2c
        super().__del__()

    def clear_display(self, send: bool = True):
        ''' Clear the entire display '''
        self._disp.fill(0 if not self.invert else 1)
        if send:
            self._disp.show()

    def clear_text_row_config(self):
        ''' Clear the text row config '''
        self.text_buffer = []
        self._rows = []

    def add_text_row_config(self, font:str, style:str, size:str, y_offset:int=0, center:bool=False):
        ''' Add a text row to the display
            y_offset: specifies a number of pixels to leave before the line
            center: centers the line in the display (based on how many characters at max width will fit) '''
        self.load_font(font, style, size)
        font_width = self.font_width(font, style, size)
        max_chars = self._width // font_width
        # set x an y position for the start of the row
        x = (self._width - (font_width * max_chars)) // 2 if center else 0
        # if the first row, start at offset otherwise get the start of the last row, add the last row height and y_offset
        y = y_offset if len(self._rows) == 0 else self._rows[-1][3] + self._rows[-1][0] + y_offset
        if y + self.font_height(font, style, size) > self._height:
            raise MicrocomFontException(f"Unable to add text row, display height {self._height}, next row {y}, offsete {y_offset}, font height {self.font_height(font, style, size)}")
        self._rows.append((self.font_height(font, style, size) + y_offset, (font, style, size), x, y))
        self.text_buffer.append([' '] * max_chars)
        self._logger.info(f"Added line {len(self.text_buffer)} {font}_{style}_{size}, max characters {max_chars}")

    def write_display(self):
        ''' Write the text buffer to the display '''
        for line in range(len(self.text_buffer)): # pylint: disable=C0200
            font_name, font_style, font_size = self._rows[line][1]
            font_map = self._framebuf.MONO_HMSB if self.loaded_fonts[f"{font_name}_{font_style}_{font_size}"].reverse() else self._framebuf.MONO_HLSB
            row_height, x_pos, y_pos = self._rows[line][0], self._rows[line][2], self._rows[line][3] # pylint: disable=W0612
            for char in self.text_buffer[line]:
                char_bytes, char_height, char_width = self.get_char_buffer(char, font=font_name, style=font_style, size=font_size)
                frame_buffer = self._framebuf.FrameBuffer(char_bytes, char_width, char_height, font_map)
                self._disp.blit(frame_buffer, x_pos, y_pos) # y pos is sum of row start points
                x_pos += char_width
        self._disp.show()


SUPPORTED_DISPLAY_TYPES = {
    'ssd1306': {'i2c': MicrocomDisplayI2C_SSD1306},
    'pcf8574': {'i2c': MicrocomDisplayI2C_PFC8574}
}
