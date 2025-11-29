from micropython import const
import rp2
from rp2 import PIO, asm_pio
  

@rp2.asm_pio()
def read_pwm():
    set(x,0)            # set scratch register to 0;
    wait(0, pin, 0)     # Do {} While ( pin == 1 ); start off by knowing we're at a low
    wait(1, pin, 0)     # Do {} While ( pin == 0 ); wait until it goes high, confirming leading edge has happened
    
    label("highloop")      #   <--.
    jmp(x_dec, "highnext") # 1    | x-- if x non-zero --.
    label("highnext")      #      |                  <--- 
    jmp(pin, "highloop")   # 2 ---' if pin still high, loop again
    
    label("lowloop")       #   <--. else pin is confirmed low
    jmp(pin, "done")       # 1    | if pin back to high, jump to done label, 
    jmp(x_dec, "lowloop")  # 2 ---' else x-- if x non-zero
    
    label("done")
    mov(isr, x)            # move the value of x to the isr
    push(isr, block)       # push the isr to the TX FIFO if not full

