'''
Micropython class to extend the asyncio Lock class to allow for 'with:' routies.
Eliminates the need to acquire and release locks, and works like CPython.

i.e.:

_lock = AsyncLock()
with _lock:
    [do some tasks]

'''

from asyncio import Lock # pylint: disable=E0611

class AsyncLock(Lock):
    ''' Extension of asyncio.Lock class to allow 'async with... :' functionality '''
    def __init__(self):
        super().__init__()
        self.__lock_obj = Lock()

    async def __aenter__(self):
        ''' Async enter function is run when entering the with and acquires the asyncio Lock object '''
        await self.__lock_obj.acquire()

    async def __aexit__(self, *args, **kwargs):
        ''' Async exit function is run when exiting the with and releases the asyncio Lock object '''
        self.__lock_obj.release()
