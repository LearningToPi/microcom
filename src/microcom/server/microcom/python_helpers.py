'''
Collection of functions to act as a replacement for base python functions
'''
from random import randint

__VERSION__ = (0,0,1)

def rjust(value, min_width, fill_char='0'):
    ''' convert value to a string of a min width using the fill char '''
    ret_val = str(value)
    if len(ret_val) < min_width:
        ret_val = (fill_char * (min_width - len(ret_val))) + ret_val
    return ret_val

def uuid4(bits=16):
    ''' creates a random number based on the bits provided in place of a uuid '''
    return randint(0, 2**bits)
