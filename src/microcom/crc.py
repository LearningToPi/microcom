''' Class to handle a streaming CRC '''
# Adapted from https://gist.github.com/Lauszus/6c787a3bc26fea6e842dfb8296ebd630

def reflect_data(x, width):
    # See: https://stackoverflow.com/a/20918545
    if width == 8:
        x = ((x & 0x55) << 1) | ((x & 0xAA) >> 1)
        x = ((x & 0x33) << 2) | ((x & 0xCC) >> 2)
        x = ((x & 0x0F) << 4) | ((x & 0xF0) >> 4)
    elif width == 16:
        x = ((x & 0x5555) << 1) | ((x & 0xAAAA) >> 1)
        x = ((x & 0x3333) << 2) | ((x & 0xCCCC) >> 2)
        x = ((x & 0x0F0F) << 4) | ((x & 0xF0F0) >> 4)
        x = ((x & 0x00FF) << 8) | ((x & 0xFF00) >> 8)
    elif width == 32:
        x = ((x & 0x55555555) << 1) | ((x & 0xAAAAAAAA) >> 1)
        x = ((x & 0x33333333) << 2) | ((x & 0xCCCCCCCC) >> 2)
        x = ((x & 0x0F0F0F0F) << 4) | ((x & 0xF0F0F0F0) >> 4)
        x = ((x & 0x00FF00FF) << 8) | ((x & 0xFF00FF00) >> 8)
        x = ((x & 0x0000FFFF) << 16) | ((x & 0xFFFF0000) >> 16)
    else:
        raise ValueError('Unsupported width')
    return x


class crc_base:
    ''' Class to handle a streaming CRC '''
    def __init__(self, data, n, poly, crc=0, ref_in=False, ref_out=False, xor_out=0):
        self.n, self.poly, self._crc, self.ref_in, self.ref_out, self.xor_out = n, poly, crc, ref_in, ref_out, xor_out
        self.update(data)

    def update(self, data):
        ''' Add data to the CRC '''
        g = 1 << self.n | self.poly  # Generator polynomial

        # Loop over the data
        for d in data:
            # Reverse the input byte if the flag is true
            if self.ref_in:
                d = reflect_data(d, 8)

            # XOR the top byte in the CRC with the input byte
            self._crc ^= d << (self.n - 8)

            # Loop over all the bits in the byte
            for _ in range(8):
                # Start by shifting the CRC, so we can check for the top bit
                self._crc <<= 1

                # XOR the CRC if the top bit is 1
                if self._crc & (1 << self.n):
                    self._crc ^= g

    @property
    def crc(self):
        ''' Return the crc of the data captured so far '''
        # Reverse the output if the flag is true
        temp = reflect_data(self._crc, self.n) if self.ref_out else self._crc

        # Return the CRC value
        return temp ^ self.xor_out

class crc32(crc_base):
    ''' CRC-32C '''
    def __init__(self, data):
        super().__init__(data, 32, 0x1EDC6F41, crc=0xFFFFFFFF, ref_in=True, ref_out=True, xor_out=0xFFFFFFFF)

class crc32_bzip2(crc_base):
    ''' CRC-32/BZIP2 '''
    def __init__(self, data):
        super().__init__(data, 32, 0x04C11DB7, crc=0xFFFFFFFF, xor_out=0xFFFFFFFF)
