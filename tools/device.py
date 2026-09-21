"""Mac USB transport. Feed the hardware watchdog for each bounded operation."""
import glob
import hashlib
import os
import termios
from .raw_repl import Pico
from .workflow import safe_name


def ports():
    return sorted(glob.glob('/dev/cu.usbmodem*'))


def select_port(explicit=None):
    if explicit:
        return explicit
    available = ports()
    if len(available) != 1:
        raise ValueError('Specify --port; expected one USB device, found %d' % len(available))
    return available[0]


class Device:
    def __init__(self, port):
        self.raw = Pico(port)
        try:
            board = self.raw.execute("import os\nprint(os.uname().machine)").decode().strip()
            if board != 'Raspberry Pi Pico W with RP2040':
                raise ValueError('Unsupported board (expected Raspberry Pi Pico W with RP2040)')
            self.raw.execute("import machine,ubinascii,os\n_tool_wdt=globals().get('wdt')\nif _tool_wdt is None: _tool_wdt=machine.WDT(timeout=8000)\n_tool_wdt.feed()")
        except BaseException:
            self.raw.close()
            raise

    def execute(self, code):
        return self.raw.execute('_tool_wdt.feed()\n' + code + '\n_tool_wdt.feed()', timeout=6)

    def read(self, name):
        safe_name(name)
        code = """try:
 with open(%r,'rb') as f: _data=f.read()
 print(ubinascii.hexlify(_data).decode())
 del _data
except OSError as e:
 if e.args[0] == 2: print('ABSENT')
 else: raise
""" % name
        text = self.execute(code).decode().strip()
        return None if text == 'ABSENT' else bytes.fromhex(text)

    def stage(self, name, data):
        safe_name(name)
        parents = name.split('/')[:-1]
        for i in range(len(parents)):
            parent = '/'.join(parents[:i+1])
            self.execute("try: os.mkdir(%r)\nexcept OSError as e:\n if e.args[0] != 17: raise" % parent)
        self.execute("_upload=open(%r,'wb')" % (name + '.new'))
        try:
            for offset in range(0, len(data), 512):
                self.execute("_upload.write(ubinascii.unhexlify(%r))" % data[offset:offset+512].hex())
        finally:
            self.execute('_upload.close(); os.sync()')
        actual = self.read(name + '.new')
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise OSError('Staged data did not match: ' + name)
        if name.endswith('.py'):
            self.execute("compile(open(%r).read(),%r,'exec')" % (name + '.new', name))

    def install(self, name):
        safe_name(name)
        self.execute("os.rename(%r,%r); os.sync()" % (name + '.new', name))

    def remove(self, name):
        safe_name(name)
        self.execute("try: os.remove(%r)\nexcept OSError as e:\n if e.args[0] != 2: raise\nos.sync()" % name)

    def close(self):
        # A hardware reset clears the enabled WDT and reloads installed modules.
        # Do not soft-reset: that does not reliably stop a hardware watchdog.
        try:
            self.raw.write(b'machine.reset()\x04')
        finally:
            try:
                termios.tcsetattr(self.raw.fd, termios.TCSANOW, self.raw.old)
            except OSError:
                pass
            os.close(self.raw.fd)
