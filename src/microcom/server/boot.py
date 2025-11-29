# _common/boot.py - runs on boot-up
import os
import re
import machine # type: ignore pylint: disable=E0401

__VERSION__ = (0,0,1)

print('==============================')
print('Booting...')
print('    ' + re.sub(', ', '\n    ',
        re.sub("[()']", "",
            str(os.uname()))
            ))
print(f"CPU Freq: {machine.freq() / 1000 / 1000 } Mhz")
print('==============================')


# activate the onboard LED
LED_STATUS_PIN = None
if os.uname().sysname == 'rp2' and not "Pi Pico W" in os.uname().machine:
    LED_STATUS_PIN = 25
elif os.uname().sysname == 'rp2':
    # PICO W
    LED_STATUS_PIN = 'LED'
elif os.uname().sysname == 'esp32':
    LED_STATUS_PIN = 2
if LED_STATUS_PIN is not None:
    machine.Pin(LED_STATUS_PIN, machine.Pin.OUT).on()
