# pyright: reportUndefinedVariable=false
from microcom.log_manager import MicrocomLogManager, INFO
from microcom.exceptions import MicrocomPIOException
from _thread import allocate_lock
from time import sleep
import asyncio
import machine # type: ignore

try:
    import rp2
except:
    raise NotImplementedError("Platform does not support PIO")

## pylint: skip-file
## type: ignore

@rp2.asm_pio(fifo_join=rp2.PIO.JOIN_RX) # type: ignore
def read_pwm():
    ''' 
    Read the number of loops for a high and low cycle, both are returned by returning 16 bits in the ISR.
    High and low cycle values are represented as 0 - number of loops (due to PIO lack of add), the value
    must be converted back to a positive integer.

        ISR (32bit return value that can be retrieved using StateMachine.get() ):
            1st 16 bits: number of loops the signal was low
            2nd 16 bits: number of loops the signal was high

    NOTE:  A loop takes 3 clock cycles for both the low and high.  Make sure to multiple the cycle
        count by 3 to get the number of clock cycles!

    Once the signal has made a full loop from low to high and back to low, the value is returned.

    The PIO function will fill the RX FIFO queue and once the queue is full the function will block and
    wait for space in the queue to make another calculation.  It is HIGHLY recommended that you empty all messages
    from the queue prior to collecting your readings.  Once the queue is full, processing will stop and the
    data will become stale.  From MicroPython the following will empty any stale messages in the queue:

    messages_queued = StateMachine.rx_fifo()
    for _ in range(messages_queued):
        StateMachine.get()

    In order to return both the low and high cycle count to MicroPython, only 16 bits from each cycle value
    can be used (the ISR is 32bits).  This means that the low and high cycle count must be within a 16 bit
    value.  If this is exceeded the value will loop and your data will be meaningless.  When creating the
    statemachine, you can select the clock frequency of the PIO.

    The maximum length of a low or high signal can be calculated as follows:

        [max loop count value] * [clock cycles per loop] * [frequency of the PIO]
                65535          *            3            /  [user configurable] = 

    Example:

    PIO set to 100Khz:
        MAX length of low OR high = 65535 * 3 / 100kHz = 1.96 seconds
        MIN length of low OR high = 1 * 3 / 100kHz = .00003 seconds (.03 milliseconds)

    A frequency of 1MHz can support:
        MAX length of low OR high = 65535 * 3 / 1MHz = 0.196605 seconds
        MIN length of low OR high = 1 * 3 / 1Mhz = .000003 seconds (.003 milliseconds)

    Make sure to set a frequency that matches your expected range!

    Sample code:

    import rp2, machine

    # create the instance (also loads the code to the PIO)
    sm0 = rp2.StateMachine(0, read_pwm, freq=100_000, in_base=machine.Pin([pin_int], machine.Pin.IN), jmp_pin=machine.Pin([pin_int]))

    # starts the state machine running (status can be checked with sm0.active() )
    sm0.active(1)

    # sample function to split the low and high counters and return a total number of clock cycles for the low and high signal
    LOW_INSTRUCTION_COUNT = 3
    HIGH_INSTRUCTION_COUNT = 3
    def parse_pwm_low_high(data:int) -> tuple[int, int]:
        """ Seperate the 1st and 2nd 16 bits and return """
        value1 = data >> 16
        value2 = data & 0b1111_1111_1111_1111
        return (((1 << 16) - value1) * LOW_INSTRUCTION_COUNT, ((1 << 16) - value2) * HIGH_INSTRUCTION_COUNT)

    # sample loop that clears any stale data in the queue, then collects 20 values, sorts and averages the highest 10
    while True:
        readings = []
        for _ in range(sm0.rx_fifo()):
            null = sm0.get()
        for _ in range(20):
            readings.append(parse_pwm_low_high(sm0.get()))
        rpms = [get_rpm(x) for x in readings]
        rpms.sort()
        print(sum(rpms[0:10]) / 10)

    NOTE: The averaging of the 10 highest was added to account for data values that were low for the 12v fan RPM this
        was written to read.  Use your own judgement in what data should be considered.
        
      '''
    # reset the counter
    set(x, 0) # type: ignore
    set(y, 0) # type: ignore
    # wait for the pin to be high
    wait(1, pin, 0)
    # wait for the pin to return low (so we are starting at a known point)
    wait(0, pin, 0)
    
    # loop until pin goes high, decrement X counter (increment not supported)
    label("pin_low_start")
    jmp(x_dec, "pin_low_continue")
    label("pin_low_continue")
    jmp(pin, "pin_high_start")
    jmp(0, "pin_low_start")
    
    # pin is high, loop until it goes low, decrement Y counter
    label("pin_high_start")
    nop()
    jmp(y_dec, "pin_high_continue")
    label("pin_high_continue")
    jmp(pin, "pin_high_start")
    
    # reached the return point, return the counter, pause if the queue is full
    label("end")
    #mov(isr, x)
    in_(x, 16)
    in_(y, 16)
    push(isr, block)


LOW_INSTRUCTION_COUNT = 3
HIGH_INSTRUCTION_COUNT = 3

class PioPWM:
    ''' Class to represent a PIO Based PWM input '''
    def __init__(self, pin:int, sm_id:int=0, freq:int=100_000, active:bool=True, log_level=INFO):
        self._logger = MicrocomLogManager(log_level)
        self._sm_id, self._freq, self._pin = sm_id, freq, pin
        self._logger.info(f"Initializing PIO State Machine {sm_id} type {self.__class__.__name__} with read_pwm function at {freq} Hz using pin {pin}")
        self.state_machine = rp2.StateMachine(sm_id, read_pwm, freq=freq, in_base=machine.Pin(pin, machine.Pin.IN), jmp_pin=machine.Pin(pin)) # type: ignore
        self._lock = allocate_lock()
        self._max_delay = (1/self._freq) * 5
        if active:
            self.start()

    def __del__(self):
        self.stop()

    def __repr__(self):
        ''' Return a string representation of the class '''
        return f"{self.__class__.__name__}(sm_id={self._sm_id}, pin={self._pin}, active={self.active}, freq={self._freq})"

    @property
    def active(self) -> bool:
        ''' Return True if the state machine is active '''
        return bool(self.state_machine.active())

    def start(self, clear_rx_buffer:bool=True):
        ''' Start the state machine '''
        with self._lock:
            if not self.active:
                self.state_machine.active(1)
            if self.active:
                self._logger.info(f"PIO State Machine {self._sm_id} started type {self.__class__.__name__}")
            else:
                self._logger.error(f"Failed to start PIO State Machine {self._sm_id}")
                raise MicrocomPIOException(f"Failed to start PIO State Machine {self._sm_id} type {self.__class__.__name__}")
        if clear_rx_buffer:
            self.clear_rx_buffer()

    def stop(self, clear_rx_buffer:bool=True):
        ''' Stop the state machine '''
        with self._lock:
            if self.active:
                self._logger.info(f"Stopping PIO State Machine {self._sm_id}")
                self.state_machine.active(0)
            if not self.active:
                self._logger.info(f"PIO State Machine {self._sm_id} stopped")
            else:
                self._logger.error(f"Failed to stop PIO State Machine {self._sm_id}")
                raise MicrocomPIOException(f"Failed to stop PIO State Machine {self._sm_id} type {self.__class__.__name__}")
        if clear_rx_buffer:
            self.clear_rx_buffer()

    def restart(self):
        ''' Restart the state machine '''
        self.stop()
        self.start()

    def clear_rx_buffer(self):
        ''' Clears the RX buffer of all queued messages '''
        with self._lock:
            self._logger.debug(f"Clearing state machine {self._sm_id} type {self.__class__.__name__} rx buffer")
            for _ in range(self.state_machine.rx_fifo()):
                null = self.state_machine.get()

    async def _read_queue(self, wait:bool=True) -> int:
        ''' wait for a reading from the rx queue '''
        if not self.active:
            self._logger.error(f"State machine {self._sm_id} type {self.__class__.__name__} read request but not active")
            raise MicrocomPIOException(f"State machine {self._sm_id} type {self.__class__.__name__} read request but not active")
        with self._lock:
            if self.state_machine.rx_fifo() > 0:
                data = self.state_machine.get()
                self._logger.debug(f"Reading from state machine {self._sm_id} type {self.__class__.__name__} value {data}")
                return data
        if not wait:
            self._logger.error(f"State machine {self._sm_id} type {self.__class__.__name__} read with no wait, no data available")
            raise MicrocomPIOException(f"State machine {self._sm_id} type {self.__class__.__name__} read with no wait, no data available")
        # a messages wasn't waiting, loop and wait for it
        wait_time = self._max_delay
        for _ in range(10):
            await asyncio.sleep(.1)
            with self._lock:
                if self.state_machine.rx_fifo() > 0:
                    data = self.state_machine.get()
                    self._logger.debug(f"Reading from state machine {self._sm_id} type {self.__class__.__name__} value {data}")
                    return data
        self._logger.error(f"State machine {self._sm_id} type {self.__class__.__name__} read timeout")
        raise MicrocomPIOException(f"State machine {self._sm_id} type {self.__class__.__name__} read timeout")

    async def get_reading(self, count:int=10, wait:bool=True, clear_rx_buffer:bool=True) -> tuple:
        ''' Get the reading as a tuple with the high and low loop counter ticks '''
        if clear_rx_buffer:
            self.clear_rx_buffer()
        readings = []
        self._logger.debug(f"Reading from state machine {self._sm_id} type {self.__class__.__name__} count {count}")
        for _ in range(count):
            readings.append(await self._read_queue(wait=wait))
        # convert the readings data to tuple of high and low clock cycles
        return tuple(
            (
                ((1 << 16) - (x >> 16)) * LOW_INSTRUCTION_COUNT,
                ((1 << 16) - (x & 0b1111_1111_1111_1111)) * HIGH_INSTRUCTION_COUNT
            )
            for x in readings
        )

    async def get_duty_u16(self, count:int=10, wait:bool=True, clear_rx_buffer:bool=True) -> int:
        ''' Get the duty of the PWM signal using a 16 bit unsigned int '''
        readings = await self.get_reading(count=count, wait=wait, clear_rx_buffer=clear_rx_buffer)
        # get sum of all the high and low counts
        low_count = sum((x[0] for x in readings))
        high_count = sum((x[1] for x in readings))
        total = low_count + high_count
        duty = int(round(high_count / total * 65536, 0))
        self._logger.debug(f"State machine {self._sm_id} type {self.__class__.__name__} PWM Duty reading {duty} ({high_count} / {total})")
        return duty

    async def get_duty_percent(self, count:int=10, wait:bool=True, clear_rx_buffer:bool=True) -> float:
        ''' Get the duty of the PWM signal as a percentage '''
        readings = await self.get_reading(count=count, wait=wait, clear_rx_buffer=clear_rx_buffer)
        # get sum of all the high and low counts
        low_count = sum((x[0] for x in readings))
        high_count = sum((x[1] for x in readings))
        total = low_count + high_count
        duty = round(high_count / total * 100, 2)
        self._logger.debug(f"State machine {self._sm_id} type {self.__class__.__name__} PWM Duty {duty}% ({high_count} / {total})")
        return duty


class PioTach(PioPWM):
    ''' Class to extend the PWM to calculate the RPM from a sensor '''
    def __init__(self, pin: int, pulse_per_rev:int, sm_id: int=0, freq: int=100000, active: bool=True, log_level=INFO):
        super().__init__(pin, sm_id, freq, active, log_level)
        self.pulse_per_rev = pulse_per_rev

    async def get_rpm(self, count:int=10, wait:bool=True, clear_rx_buffer:bool=True) -> float:
        ''' Get the current RPM using the average of the reading count specified '''
        readings = await self.get_reading(count=count, wait=wait, clear_rx_buffer=clear_rx_buffer)
        revolutions = len(readings) / self.pulse_per_rev
        total_time = (sum((sum(x) for x in readings)) / revolutions) / self._freq
        return round(60 / total_time, 2)
