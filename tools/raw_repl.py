"""Local USB raw-REPL helper; Python standard library only."""
import os
import select
import termios
import time


class Pico:
    def __init__(self, port):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.old = termios.tcgetattr(self.fd)
        self.buffer = b''
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.write(b'\r\x03\x03')
        time.sleep(0.2)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.write(b'\x01')
        self.until(b'raw REPL; CTRL-B to exit\r\n>')

    def write(self, data):
        while data:
            if select.select([], [self.fd], [], 5)[1]:
                n = os.write(self.fd, data[:256])
                data = data[n:]
            else:
                raise TimeoutError('USB write timeout')

    def until(self, marker, timeout=10):
        deadline = time.monotonic() + timeout
        while marker not in self.buffer:
            if time.monotonic() >= deadline:
                raise TimeoutError('USB response timeout')
            if select.select([self.fd], [], [], 0.1)[0]:
                self.buffer += os.read(self.fd, 4096)
        before, self.buffer = self.buffer.split(marker, 1)
        return before

    def execute(self, code, timeout=10):
        self.write(code.encode() + b'\x04')
        ack = self.until(b'OK')
        if ack:
            raise RuntimeError('Unexpected raw-REPL acknowledgement')
        out = self.until(b'\x04', timeout)
        err = self.until(b'\x04', timeout)
        self.until(b'>')
        if err.strip():
            # Traceback could contain secret-bearing source; never print it.
            raise RuntimeError('Pico execution failed (device traceback withheld)')
        return out

    def close(self):
        self.write(b'\x02')
        termios.tcsetattr(self.fd, termios.TCSANOW, self.old)
        os.close(self.fd)
