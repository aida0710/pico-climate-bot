"""Bounded, streaming seven-day climate history for MicroPython.

Records are Unix seconds plus tenths of a degree / percent and CRC32. Invalid
measurements become missing values; invalid or non-increasing times are rejected.
The default 2016 records cover seven days at five-minute sampling intervals.
"""

import binascii
import os
import struct

_RECORD_SIZE = 12
_MISSING = -32768
_MIN_EPOCH = 1704067200  # 2024-01-01 UTC
_WEEK = 7 * 86400


def _valid_epoch(value):
    return (isinstance(value, int) and not isinstance(value, bool)
            and _MIN_EPOCH <= value <= 0xffffffff)


def _measurement(value, lower, upper):
    # Ordered comparisons also reject NaN and infinities without importing math.
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and lower <= value <= upper):
        return int(round(value * 10))
    return _MISSING


def _decode(data):
    if len(data) != _RECORD_SIZE:
        return None
    epoch, temp, humidity, crc = struct.unpack('<IhhI', data)
    if ((binascii.crc32(data[:8]) & 0xffffffff) != crc
            or not _valid_epoch(epoch)
            or (temp != _MISSING and not -400 <= temp <= 850)
            or (humidity != _MISSING and not 0 <= humidity <= 1000)):
        return None
    return epoch, temp, humidity


class RingHistory:
    """One fixed-size file; reads never collect the history in RAM.

    Use a single writer and consume iter_records before appending again. I/O
    errors propagate so the application can log them and continue sensing.
    Existing oversized files are rejected rather than silently discarded.
    """

    def __init__(self, path='climate.bin', capacity=2016, feed=lambda: None):
        if (not isinstance(capacity, int) or isinstance(capacity, bool)
                or not 1 <= capacity <= 2048):
            raise ValueError('history capacity must be 1..2048 records')
        self.path = path
        self.capacity = capacity
        self.feed = feed
        self._next = 0
        self._latest = 0
        try:
            stream = open(path, 'rb')
        except OSError as exc:
            if exc.args[0] != 2:  # ENOENT; do not replace unreadable files.
                raise
            with open(path, 'wb'):
                pass
            return
        with stream:
            stream.seek(0, 2)
            if stream.tell() > capacity * _RECORD_SIZE:
                raise ValueError('history file exceeds configured capacity')
            stream.seek(0)
            for index in range(capacity):
                if index % 64 == 0:
                    self.feed()
                raw = stream.read(_RECORD_SIZE)
                if len(raw) != _RECORD_SIZE:
                    break
                row = _decode(raw)
                if row is not None and row[0] > self._latest:
                    self._latest = row[0]
                    self._next = (index + 1) % capacity

    def append(self, now, temp, humidity):
        """Persist a sample, returning False for invalid/non-increasing time."""
        if not _valid_epoch(now) or now <= self._latest:
            return False
        payload = struct.pack('<Ihh', now, _measurement(temp, -40, 85),
                              _measurement(humidity, 0, 100))
        data = payload + struct.pack('<I', binascii.crc32(payload) & 0xffffffff)
        self.feed()
        with open(self.path, 'r+b') as stream:
            stream.seek(self._next * _RECORD_SIZE)
            if stream.write(data) != _RECORD_SIZE:
                raise OSError('short history write')
            stream.flush()
        sync = getattr(os, 'sync', None)
        if sync is not None:
            sync()
        self._latest = now
        self._next = (self._next + 1) % self.capacity
        return True

    def iter_records(self, now):
        """Yield (epoch, temperature, humidity) oldest first in [now-7d, now]."""
        if not _valid_epoch(now):
            return
        cutoff = now - _WEEK
        previous = 0
        with open(self.path, 'rb') as stream:
            for offset in range(self.capacity):
                if offset % 64 == 0:
                    self.feed()
                index = (self._next + offset) % self.capacity
                stream.seek(index * _RECORD_SIZE)
                row = _decode(stream.read(_RECORD_SIZE))
                if row is None or row[0] <= previous:
                    continue
                epoch, temp, humidity = row
                previous = epoch
                if cutoff <= epoch <= now:
                    yield (epoch, None if temp == _MISSING else temp / 10,
                           None if humidity == _MISSING else humidity / 10)
