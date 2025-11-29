'''
Class to manage tasks to run on a second core
'''

import _thread
import asyncio
from time import sleep
from gc import collect
from microcom.log_manager import MicrocomLogManager
from microcom.msg._base import MicrocomMsg, MSG_TYPE_ASYNC_REPLY


QUEUE_LIMIT = 5
QUEUE_CHECK_INTERVAL = .25
THREAD_CHECK_INTERVAL = .5

TASK_FUNCTION = 0
TASK_ARGS = 1
TASK_KWARGS = 2
TASK_COUNT = 3
TASK_DELAY = 4


class WorkerThread:
    ''' Class to handle a worker thread performing tasks passed by an asyncio main thread '''
    def __init__(self, logger:MicrocomLogManager, reply_func, queue_limit=QUEUE_LIMIT, queue_check_interval=QUEUE_CHECK_INTERVAL,
                 thread_check_interval=THREAD_CHECK_INTERVAL):
        self._logger, self.queue_limit, self._reply_func = logger, queue_limit, reply_func
        self.queue_check_interval, self.thread_check_interval = queue_check_interval, thread_check_interval
        self._queue = []
        self._queue_lock = _thread.allocate_lock()
        self._reply_queue = []
        self._reply_queue_lock = _thread.allocate_lock()
        self._logger.info(f"{self.__class__.__name__}: Starting thread watcher...")
        self._worker_thread = None
        asyncio.create_task(self.thread_watcher())

    async def thread_watcher(self):
        ''' Asyncio thread on the primary core to monitor for data returned by the worker thread '''
        while True:
            try:
                if self._worker_thread is None:
                    self._logger.info(f"{self.__class__.__name__}: Starting worker thread...")
                    self._worker_thread = _thread.start_new_thread(self.__worker_thread, ())
                while len(self._reply_queue) > 0:
                    while self._reply_queue_lock.locked():
                        await asyncio.sleep(self.queue_check_interval)
                    with self._reply_queue_lock:
                        reply_data = self._reply_queue.pop(0)
                    message = MicrocomMsg(msg_type=MSG_TYPE_ASYNC_REPLY, data=reply_data)
                    self._reply_func(message)
                await asyncio.sleep(self.thread_check_interval)
            except Exception as e:
                self._logger.error(f"{self.__class__.__name__}: Error in thread watcher: {e.__class__.__name__}: {e}")

    async def add_task(self, function, *args, _count:int=1, _delay:int=0, **kwargs):
        ''' Add a task to the queue for the worker thread '''
        while self._queue_lock.locked():
            await asyncio.sleep(self.queue_check_interval)
        with self._queue_lock:
            if len(self._queue) < self.queue_limit:
                self._queue.append([function, args, kwargs, _count, _delay])
            else:
                self._logger.error(f"{self.__class__.__name__}: Error adding task for {function}. Queue is full")

    def sync_add_task(self, function, *args, _count:int=1, _delay:int=0, **kwargs):
        ''' Add a task to the queue without asyncio '''
        with self._queue_lock:
            if len(self._queue) < self.queue_limit:
                self._queue.append([function, args, kwargs, _count, _delay])
            else:
                self._logger.error(f"{self.__class__.__name__}: Error adding task for {function}. Queue is full")

    async def clear_queue(self):
        ''' Clear all items in the worker thread queue '''
        while self._queue_lock.locked():
            await asyncio.sleep(self.queue_check_interval)
        with self._queue_lock:
            self._queue = []

    def __worker_thread(self):
        ''' Thread to run on the second core. The thread will check for tasks in the queue to execute. Once a task has been completed, the
            return data is placed in the reply_queue for the main thread to send back to the client '''
        while True:
            try:
                while len(self._queue) > 0:
                    with self._queue_lock:
                        task = self._queue.pop(0)
                    # execute the task
                    while task[TASK_COUNT] > 0:
                        collect()
                        return_data = task[TASK_FUNCTION](*task[TASK_ARGS], **task[TASK_KWARGS])
                        task[TASK_COUNT] -= 1
                        if task[TASK_COUNT] > 0:
                            sleep(task[TASK_DELAY])

                    # return the data
                    with self._reply_queue_lock:
                        self._reply_queue.append({'function': task[TASK_FUNCTION], 'args': task[TASK_ARGS], 'kwargs': task[TASK_KWARGS], 'return': return_data})

                # if the queue is empty, wait for the check interval and check for more data
                sleep(self.thread_check_interval)
            except Exception as e:
                self._logger.error(f"{self.__class__.__name__}: Worker Thread: Error occured: {e.__class__.__name__}: {e}")
