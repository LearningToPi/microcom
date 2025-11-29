''' Micropython functions to mimic the logging class on Python '''
from time import localtime, time
from microcom.python_helpers import rjust

__VERSION__ = (1, 0, 3)

EMERGENCY = 0
ALERT = 1
CRITICAL = 2
ERROR = 3
WARNING = 4
NOTICE = 5
INFO = 6
DEBUG = 7

LOG_LVL_STR = ('EMERGENCY', 'ALERT', 'CRITICAL', 'ERROR', 'WARNING', 'NOTICE', 'INFO', 'DEBUG')

LOGGING_CONFIG = ('console_level', 'log_file', 'file_level', 'file_mode', 'tz_str', 'tz_offset')

handler = None
def create_handler():
    global handler
    if handler is None:
        handler = logging_handler()

def debug(message):
    create_handler()
    handler.debug(message) # type: ignore

def info(message):
    create_handler()
    handler.info(message) # type: ignore

def notice(message):
    create_handler()
    handler.notice(message) # type: ignore

def warning(message):
    create_handler()
    handler.warning(message) # type: ignore

def critical(message):
    create_handler()
    handler.critical(message) # type: ignore

def alert(message):
    create_handler()
    handler.alert(message) # type: ignore

def emergency(message):
    create_handler()
    handler.emergency(message) # type: ignore


class logging_handler:
    def __init__(self,console_level=INFO, log_file='', file_level=INFO, tz_str=None, tz_offset=0):
        self.console_level = console_level if isinstance(console_level, int) else LOG_LVL_STR.index(console_level.upper())
        self.file_level = file_level if isinstance(file_level, int) else LOG_LVL_STR.index(file_level)
        self.tz_str = tz_str
        self.tz_offset = tz_offset
        if log_file is not '':
            self._log_file = open(log_file, mode='a', encoding="utf-8")
        else:
            self._log_file = None

    def __del__(self):
        if self._log_file is not None:
            self._log_file.close()

    def log(self, message:str, level:int, *args):
        ''' print the log message '''
        now = localtime(time() + self.tz_offset * 3600)
        time_str = f'{rjust(now[0],4)}-{rjust(now[1],2)}-{rjust(now[2],2)} {rjust(now[3],2)}:{rjust(now[4],2)}:{rjust(now[5],2)} {"UTC" if self.tz_offset == 0 else self.tz_str}'
        if len(args) > 0:
            arg_pos = 0
            while r'%s' in message or r'%i' in message or r'%f' in message:
                if r'%s' in message:
                    message = message.replace(r'%s', str(args[arg_pos]), 1)
                elif r'%i' in message:
                    message = message.replace(r'%i', str(args[arg_pos]), 1)
                elif r'%f' in message:
                    message = message.replace(r'%f', str(args[arg_pos]), 1)
                arg_pos += 1
        out_line = f"{time_str} - {LOG_LVL_STR[level]} - {message}"
        if level <= self.console_level:
            print(out_line)
        if level <= self.file_level and self._log_file is not None:
            self._log_file.write(out_line)

    def emergency(self, message:str, *args):
        self.log(message, EMERGENCY, *args)

    def alert(self, message:str, *args):
        self.log(message, ALERT, *args)

    def critical(self, message:str, *args):
        self.log(message, CRITICAL, *args)

    def error(self, message:str, *args):
        self.log(message, ERROR, *args)

    def warning(self, message:str, *args):
        self.log(message, WARNING, *args)

    def notice(self, message:str, *args):
        self.log(message, NOTICE, *args)

    def info(self, message:str, *args):
        self.log(message, INFO, *args)

    def debug(self, message:str, *args):
        self.log(message, DEBUG, *args)

def create_logger(*args, **kwargs):
    ''' Used to emulate the create_logger library in python '''
    return logging_handler(*args, **kwargs)
