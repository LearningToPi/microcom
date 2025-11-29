'''
Class to handle logging for Microcom.  Logging handler will include storing errors that occur in a buffer that
will be sent to the client.
'''

# pylint: disable=W0221
from asyncio import create_task, sleep
from time import time
from libs.logging_handler import logging_handler, EMERGENCY, ALERT, CRITICAL, NOTICE, ERROR, WARNING, INFO, DEBUG
from microcom.async_lock import AsyncLock

LOG_LIST_MAX = 10

class MicrocomLogManager(logging_handler):
    ''' Class to store, send and print log messages '''
    def __init__(self, console_level=INFO, send_level=INFO, log_max_len=LOG_LIST_MAX, tz_str:str="GMT", tz_offset:int=0):
        super().__init__(console_level=console_level, tz_offset=tz_offset, tz_str=tz_str)
        self.__lock = AsyncLock()
        self.__logs = []
        self.log_max_len = log_max_len
        self.send_level = send_level

    async def __log_message(self, level:int, message:str, no_send=False):
        if self.console_level >= level:
            super().log(message=message, level=level)
        if self.send_level >= level and not no_send:
            async with self.__lock:
                while len(self.__logs) > self.log_max_len:
                    self.__logs.pop(0)
                self.__logs.append({'time': time(), 'level': level, 'message': message, 'sent': False})

    def emergency(self, message, no_send=False):
        create_task(self.__log_message(EMERGENCY, message, no_send))

    def alert(self, message, no_send=False):
        create_task(self.__log_message(ALERT, message, no_send))

    def critical(self, message, no_send=False):
        create_task(self.__log_message(CRITICAL, message, no_send))

    def error(self, message, no_send=False):
        create_task(self.__log_message(ERROR, message, no_send))

    def warning(self, message, no_send=False):
        create_task(self.__log_message(WARNING, message, no_send))

    def notice(self, message, no_send=False):
        create_task(self.__log_message(NOTICE, message, no_send))

    def info(self, message, no_send=False):
        create_task(self.__log_message(INFO, message, no_send))

    def debug(self, message, no_send=False):
        create_task(self.__log_message(DEBUG, message, no_send))
