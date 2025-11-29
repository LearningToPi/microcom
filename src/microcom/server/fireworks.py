from machine import Pin # type: ignore # pylint: disable=E0401
import asyncio
from libs.logging_handler import logging_handler
from microcom.display import MicrocomDisplayI2C_SSD1306
from microcom.gpio import gpio_set, gpio_get, PinIRQ, IN, PULL_NONE, OUT



class FireworksController:
    ''' Class to handle switches, buttons, screen output and outputs
        Requires: 5 switches, 1 key, and 1 button as inputs,
            1 SSD1306 display (resolution 128x64),
            5 output pins for triggering:
            config (dict) = {
                sw1: [int], sw2: [int], sw3: [int], sw4: [int], sw5: [int], btn: [int], key: [int],
                out1: [int], out2: [int], out3: [int]: out4: [int]: out5: [int], led1: [int], led2: [int], ledbtn: [int]
            }
            All inputs and outputs are expected to have appropriate pull up/down resistors external to the microcontroller
            '''
    def __init__(self, logger:logging_handler, config:dict, display_id:int, _vars:dict, _byte_arr:dict):
        self._logger, self.config, self.display_id, self._vars, self._byte_arr = logger, config, display_id, _vars, _byte_arr
        asyncio.create_task(self.init())

        self.out1, self.out2, self.out3, self.out4, self.out5 = \
            Pin(config['out1']), Pin(config['out2']), Pin(config['out3']), Pin(config['out4']), Pin(config['out5'])
        
    async def init(self):
        ''' Initialize all the pins '''
        # set all the pin states
        try:
            self._logger.info(f"Initializing fireworks controller: {self.config}")
            await gpio_set({str(self.config['sw1']): {'mode': IN},
                            str(self.config['sw2']): {'mode': IN},
                            str(self.config['sw3']): {'mode': IN},
                            str(self.config['sw4']): {'mode': IN},
                            str(self.config['sw5']): {'mode': IN},
                            str(self.config['btn']): {'mode': IN},
                            str(self.config['key']): {'mode': IN},
                            str(self.config['out1']): {'mode': OUT, 'value': 0},
                            str(self.config['out2']): {'mode': OUT, 'value': 0},
                            str(self.config['out3']): {'mode': OUT, 'value': 0},
                            str(self.config['out4']): {'mode': OUT, 'value': 0},
                            str(self.config['out5']): {'mode': OUT, 'value': 0},
                            str(self.config['led1']): {'mode': OUT, 'value': 0},
                            str(self.config['led2']): {'mode': OUT, 'value': 0},
                            str(self.config['ledbtn']): {'mode': OUT, 'value': 0}}, _logger=self._logger)
            # Create IRQ handlers
            self._irq_handlers = {
                self.config['sw1']: PinIRQ(Pin(self.config['sw1']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['sw1']}, 'value')], debounce_ms=0),
                self.config['sw2']: PinIRQ(Pin(self.config['sw2']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['sw2']}, 'value')], debounce_ms=0),
                self.config['sw3']: PinIRQ(Pin(self.config['sw3']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['sw3']}, 'value')], debounce_ms=0),
                self.config['sw4']: PinIRQ(Pin(self.config['sw4']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['sw4']}, 'value')], debounce_ms=0),
                self.config['sw5']: PinIRQ(Pin(self.config['sw5']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['sw5']}, 'value')], debounce_ms=0),
                self.config['btn']: PinIRQ(Pin(self.config['btn']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['btn']}, 'value')], debounce_ms=0),
                self.config['key']: PinIRQ(Pin(self.config['key']), self._logger, rising=True, falling=True, tasks=[(self.input_irq, {'pin': self.config['key']}, 'value')], debounce_ms=0),
            }

            # init the display
            await self.status_update()
        except Exception as e:
            self._logger.error(f"Error initializing FireworksController. {e.__class__.__name__}: {e}")

    async def run(self):
        ''' Function called by Microcom after initialization '''
        await self.status_update()

    async def input_irq(self, pin:int, value:int):
        ''' Process an IRQ input'''
        self._logger.debug(f"IRQ called for pin {pin}, value {value}")
        await self.status_update()

    def __del__(self):
        ''' Delete objects '''

    async def status_update(self):
        ''' Update the status based on an external trigger '''
        # get current values
        inputs = await gpio_get([self.config['sw1'], self.config['sw2'], self.config['sw3'], self.config['sw4'], self.config['sw5'], self.config['key'], self.config['btn']], self._logger)
        sw1, sw2, sw3, sw4, sw5, key, btn = inputs[str(self.config['sw1'])].get('value'), inputs[str(self.config['sw2'])].get('value'), inputs[str(self.config['sw3'])].get('value'), \
            inputs[str(self.config['sw4'])].get('value'), inputs[str(self.config['sw5'])].get('value'), inputs[str(self.config['key'])].get('value'), inputs[str(self.config['btn'])].get('value')

        self._logger.debug(f"Updating status display. Values: {inputs}")
        # update the screen
        self._vars['display'][str(self.display_id)].write_text(line=0, text=('1 ' if sw1 else '  ') +
                                ('2 ' if sw2 else '  ') +
                                ('3 ' if sw3 else '  ') +
                                ('4 ' if sw4 else '  ') +
                                ('5 ' if sw5 else '  '), justify='c', wrap=False, send=False)
        self._vars['display'][str(self.display_id)].write_text(line=2, text='ARMED' if key else 'LOCKED', justify='c', wrap=False, send=True)

        # operate LED's / outputs
        # led2 if key enabled
        await gpio_set({str(self.config['led2']): {'value': 1 if key else 0}}, _logger=self._logger)
        # button if key and at least 1 switch enabled
        await gpio_set({str(self.config['ledbtn']): {'value': 1 if key and (sw1 or sw2 or sw3 or sw4 or sw5) else 0}}, _logger=self._logger)
        # if button and key
        if btn and key:
            # Set the display, output LED and output trigger for enabled switches
            if sw1:
                self._vars['display'][str(self.display_id)].write_text(line=1, text='FIRE!' if btn else ' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 1}, str(self.config['out1']): {'value': 1}}, _logger=self._logger)
            if sw2:
                self._vars['display'][str(self.display_id)].write_text(line=1, text='FIRE!' if btn else ' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 1}, str(self.config['out2']): {'value': 1}}, _logger=self._logger)
            if sw3:
                self._vars['display'][str(self.display_id)].write_text(line=1, text='FIRE!' if btn else ' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 1}, str(self.config['out3']): {'value': 1}}, _logger=self._logger)
            if sw4:
                self._vars['display'][str(self.display_id)].write_text(line=1, text='FIRE!' if btn else ' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 1}, str(self.config['out4']): {'value': 1}}, _logger=self._logger)
            if sw5:
                self._vars['display'][str(self.display_id)].write_text(line=1, text='FIRE!' if btn else ' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 1}, str(self.config['out5']): {'value': 1}}, _logger=self._logger)
            if not sw1 and not sw2 and not sw3 and not sw4 and not sw5:
                # if no switches, clear everything
                self._vars['display'][str(self.display_id)].write_text(line=1, text=' ', justify='c', wrap=False, send=True)
                await gpio_set({str(self.config['led1']): {'value': 0}, str(self.config['out1']): {'value': 0}, str(self.config['out2']): {'value': 0},
                                str(self.config['out3']): {'value': 0}, str(self.config['out4']): {'value': 0}, str(self.config['out5']): {'value': 0}}, _logger=self._logger)
        else:
            self._vars['display'][str(self.display_id)].write_text(line=1, text=' ', justify='c', wrap=False, send=True)
            await gpio_set({str(self.config['led1']): {'value': 0}, str(self.config['out1']): {'value': 0}, str(self.config['out2']): {'value': 0},
                            str(self.config['out3']): {'value': 0}, str(self.config['out4']): {'value': 0}, str(self.config['out5']): {'value': 0}}, _logger=self._logger)
