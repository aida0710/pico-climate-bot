import binascii
import importlib.util
import pathlib
import struct
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'firmware' / 'pico_history.py'
BASE = 1735689600
WEEK = 7 * 86400


def record(epoch, temp=200, humidity=500):
    payload = struct.pack('<Ihh', epoch, temp, humidity)
    return payload + struct.pack('<I', binascii.crc32(payload) & 0xffffffff)


class RingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.exists(), 'standalone history module is missing')
        spec = importlib.util.spec_from_file_location('pico_history', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(pathlib.Path(self.tmp.name) / 'climate.bin')
        self.sync = mock.patch.object(self.module.os, 'sync', create=True)
        self.sync_mock = self.sync.start()
        self.addCleanup(self.sync.stop)

    def history(self, capacity=4, feed=lambda: None):
        return self.module.RingHistory(self.path, capacity=capacity, feed=feed)

    def test_roundtrip_and_wire_format(self):
        h = self.history()
        self.assertTrue(h.append(BASE, 23.4, 56.7))
        self.assertTrue(h.append(BASE + 300, None, None))
        self.assertEqual(list(h.iter_records(BASE + 300)),
                         [(BASE, 23.4, 56.7), (BASE + 300, None, None)])
        self.assertEqual(pathlib.Path(self.path).read_bytes(),
                         record(BASE, 234, 567) + record(BASE + 300, -32768, -32768))
        self.assertEqual(self.sync_mock.call_count, 2)

    def test_wrap_reboot_and_next_append_preserve_order_and_bound(self):
        h = self.history()
        for i in range(13):
            self.assertTrue(h.append(BASE + i * 300, i, i))
            self.assertLessEqual(pathlib.Path(self.path).stat().st_size, 48)
        h = self.history()
        self.assertTrue(h.append(BASE + 13 * 300, 13, 13))
        self.assertEqual([r[0] for r in h.iter_records(BASE + 13 * 300)],
                         [BASE + i * 300 for i in range(10, 14)])

    def test_window_includes_boundary_and_excludes_future(self):
        h = self.history()
        for epoch in (BASE - 1, BASE, BASE + WEEK, BASE + WEEK + 1):
            h.append(epoch, 20, 50)
        self.assertEqual([r[0] for r in h.iter_records(BASE + WEEK)],
                         [BASE, BASE + WEEK])

    def test_unsynced_backward_duplicate_and_invalid_timestamps_rejected(self):
        h = self.history()
        for epoch in (0, 1704067199, True, float('nan'), 4294967296, 'bad', 1.5):
            self.assertFalse(h.append(epoch, 20, 50))
        self.assertTrue(h.append(BASE, 20, 50))
        for epoch in (BASE - 1, BASE):
            self.assertFalse(h.append(epoch, 20, 50))
        self.assertEqual(list(h.iter_records(BASE)), [(BASE, 20.0, 50.0)])

    def test_invalid_measurements_are_missing_and_boundaries_survive(self):
        h = self.history(capacity=16)
        bad = [True, False, '20', float('nan'), float('inf'), float('-inf')]
        for i, value in enumerate(bad):
            self.assertTrue(h.append(BASE + i, value, value))
        h.append(BASE + 6, -40.1, -0.1)
        h.append(BASE + 7, 85.1, 100.1)
        h.append(BASE + 8, -40, 0)
        h.append(BASE + 9, 85, 100)
        rows = list(h.iter_records(BASE + 9))
        self.assertTrue(all(row[1:] == (None, None) for row in rows[:8]))
        self.assertEqual(rows[-2:], [(BASE + 8, -40.0, 0.0), (BASE + 9, 85.0, 100.0)])

    def test_crc_damage_and_torn_tail_recovered(self):
        broken = bytearray(record(BASE + 1)); broken[4] ^= 1
        pathlib.Path(self.path).write_bytes(record(BASE) + broken + record(BASE + 2)[:7])
        h = self.history()
        self.assertEqual(list(h.iter_records(BASE + 3)), [(BASE, 20.0, 50.0)])
        h.append(BASE + 3, 21, 51)
        h = self.history()
        self.assertEqual(list(h.iter_records(BASE + 3)),
                         [(BASE, 20.0, 50.0), (BASE + 3, 21.0, 51.0)])

    def test_wrapped_newest_corruption_recovery(self):
        h = self.history()
        for i in range(6): h.append(BASE + i, 20, 50)
        with open(self.path, 'r+b') as stream:
            stream.seek(12); stream.write(b'\xff' * 12)
        h = self.history()
        self.assertEqual([r[0] for r in h.iter_records(BASE + 6)], [BASE + 2, BASE + 3, BASE + 4])
        h.append(BASE + 6, 20, 50)
        self.assertEqual([r[0] for r in self.history().iter_records(BASE + 6)],
                         [BASE + 2, BASE + 3, BASE + 4, BASE + 6])

    def test_valid_crc_does_not_accept_invalid_record_data(self):
        pathlib.Path(self.path).write_bytes(record(1) + record(BASE, 999, 500) + record(BASE + 1))
        self.assertEqual(list(self.history().iter_records(BASE + 1)), [(BASE + 1, 20.0, 50.0)])

    def test_capacity_limits_and_oversized_file(self):
        for value in (0, -1, 2049, True, 1.5):
            with self.assertRaises(ValueError): self.history(value)
        pathlib.Path(self.path).write_bytes(b'\0' * 49)
        with self.assertRaises(ValueError): self.history()

    def test_watchdog_fed_during_scan_and_iteration(self):
        pathlib.Path(self.path).write_bytes(b''.join(record(BASE + i) for i in range(130)))
        calls = []
        h = self.history(130, lambda: calls.append(1))
        self.assertGreaterEqual(len(calls), 2)
        calls.clear()
        self.assertEqual(sum(1 for _ in h.iter_records(BASE + 130)), 130)
        self.assertGreaterEqual(len(calls), 2)


if __name__ == '__main__': unittest.main()
