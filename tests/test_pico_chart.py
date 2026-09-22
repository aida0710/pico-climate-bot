import importlib.util
import io
import struct
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch


class FakeFrameBuffer:
    instances = []

    def __init__(self, buffer, width, height, mode):
        self.buffer, self.width, self.height = buffer, width, height
        self.texts, self.lines = [], []
        self.__class__.instances.append(self)

    def pixel(self, x, y, color=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        index = (y * self.width + x) // 2
        shift = 4 if x % 2 == 0 else 0
        if color is None:
            return (self.buffer[index] >> shift) & 15
        self.buffer[index] = (self.buffer[index] & ~(15 << shift)) | (color << shift)

    def fill(self, color):
        for i in range(len(self.buffer)):
            self.buffer[i] = color * 17

    def fill_rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, color)

    def line(self, x0, y0, x1, y1, color):
        self.lines.append((x0, y0, x1, y1, color))
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            self.pixel(
                round(x0 + (x1 - x0) * i / steps),
                round(y0 + (y1 - y0) * i / steps),
                color,
            )

    def text(self, text, x, y, color):
        self.texts.append(text)


class History:
    def __init__(self, rows):
        self.rows = rows

    def iter_records(self, now):
        yield from self.rows


def load_chart():
    spec = importlib.util.spec_from_file_location(
        "pico_chart_test_target", Path(__file__).parents[1] / "firmware/pico_chart.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        framebuf=types.SimpleNamespace(FrameBuffer=FakeFrameBuffer, GS4_HMSB=2),
    ):
        spec.loader.exec_module(module)
    return module


def decode_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + size]
        crc = struct.unpack(">I", data[pos + 8 + size : pos + 12 + size])[0]
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF
        chunks[kind] = chunks.get(kind, b"") + payload
        pos += size + 12
    return chunks, zlib.decompress(chunks[b"IDAT"])


class PicoChartTests(unittest.TestCase):
    NOW = 1790000000

    def setUp(self):
        FakeFrameBuffer.instances.clear()
        self.chart = load_chart()

    def draw(self, rows):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chart.png")
            result = self.chart.render(
                History(rows), self.NOW, path, lambda: calls.append(1)
            )
            self.assertEqual(result, path)
            data = Path(path).read_bytes()
        chunks, raw = decode_png(data)
        self.assertEqual(
            struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (480, 272, 4, 3, 0, 0, 0)
        )
        self.assertEqual(len(raw), 241 * 272)
        self.assertLess(len(data), 68000)
        self.assertGreaterEqual(len(calls), 272)
        self.assertTrue(all(raw[y * 241] == 0 for y in range(272)))
        return FakeFrameBuffer.instances[-1], raw

    def test_empty_graph_is_valid_png(self):
        fb, _ = self.draw([])
        self.assertEqual(fb.texts.count("No readings"), 2)
        self.assertTrue(any("JST" in text for text in fb.texts))

    def test_single_point_visible_and_missing_data_not_connected(self):
        fb, _ = self.draw([(self.NOW - 60, 25, None)])
        self.assertTrue(
            any(fb.pixel(x, y) == 4 for y in range(272) for x in range(480))
        )
        self.assertEqual(fb.texts.count("No readings"), 1)
        self.assertFalse(any(line[-1] in (4, 5) for line in fb.lines))

    def test_gap_and_missing_value_break_lines(self):
        fb, _ = self.draw(
            [
                (self.NOW - 3000, 20, 40),
                (self.NOW - 2700, None, 41),
                (self.NOW - 2400, 22, 42),
                (self.NOW - 60, 24, 44),
            ]
        )
        self.assertEqual(len([line for line in fb.lines if line[-1] == 4]), 0)
        self.assertEqual(len([line for line in fb.lines if line[-1] == 5]), 2)

    def test_png_preserves_high_nibble_first_pixels(self):
        fb, raw = self.draw([])
        for y in (0, 32, 100, 271):
            self.assertEqual(
                raw[y * 241 + 1 : (y + 1) * 241],
                bytes(fb.buffer[y * 240 : (y + 1) * 240]),
            )

    def test_canvas_is_only_large_allocation(self):
        sizes = []

        def allocate(size):
            sizes.append(size)
            return bytearray(size)

        with patch.object(self.chart, "bytearray", allocate, create=True):
            self.draw([])
        self.assertEqual(max(sizes), 65280)
        self.assertEqual(sum(sizes), 65280)

    def test_writer_never_buffers_image_for_output(self):
        class Sink(io.BytesIO):
            largest_write = 0

            def write(self, data):
                self.largest_write = max(self.largest_write, len(data))
                return super().write(data)

            def close(self):
                pass

        sink = Sink()
        canvas = bytearray(65280)
        canvas[0] = 0x45  # First pixel temperature, next pixel humidity.
        with patch.object(self.chart, "open", return_value=sink, create=True):
            self.chart._write_png(canvas, "unused", lambda: None)
        _, raw = decode_png(sink.getvalue())
        self.assertEqual(raw[:2], b"\x00\x45")
        self.assertLessEqual(sink.largest_write, 240)

    def test_seven_day_endpoints_and_nonfinite_values(self):
        fb, _ = self.draw(
            [
                (self.NOW - 8 * 86400, 99, 99),
                (self.NOW - 7 * 86400, 20, None),
                (self.NOW - 600, float("inf"), float("nan")),
                (self.NOW, 20, None),
                (self.NOW + 1, 99, 99),
            ]
        )
        self.assertIn("2 readings", fb.texts)
        self.assertIn("0 readings", fb.texts)
        self.assertTrue(any(fb.pixel(48, y) == 4 for y in range(57, 126)))
        self.assertTrue(any(fb.pixel(464, y) == 4 for y in range(57, 126)))


if __name__ == "__main__":
    unittest.main()
