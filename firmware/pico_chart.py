"""Small, dependency-free MicroPython PNG chart with bounded working memory."""

import binascii
import framebuf
import gc
import math
import struct
import time

WIDTH = 480
HEIGHT = 272
WINDOW = 7 * 86400
GAP = 900
# Background, panel, text, grid, temperature, humidity, subdued text.
PALETTE = bytes(
    (
        245,
        247,
        250,
        255,
        255,
        255,
        23,
        40,
        59,
        221,
        229,
        237,
        188,
        88,
        30,
        23,
        111,
        155,
        82,
        100,
        122,
    )
)


def _chunk(output, kind, data):
    output.write(struct.pack(">I", len(data)))
    output.write(kind)
    output.write(data)
    crc = binascii.crc32(data, binascii.crc32(kind)) & 0xFFFFFFFF
    output.write(struct.pack(">I", crc))


def _write_png(canvas, path, feed):
    """Stream one uncompressed DEFLATE stored block per scanline.

    GS4_HMSB has the left pixel in the high nibble, exactly as PNG expects.
    No compression API, whole-image bytes copy, or row copy is needed.
    """
    row_bytes = WIDTH // 2
    size = row_bytes + 1  # PNG filter byte followed by palette pixels.
    adler_a, adler_b = 1, 0
    view = memoryview(canvas)
    with open(path, "wb") as output:
        output.write(b"\x89PNG\r\n\x1a\n")
        _chunk(output, b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 4, 3, 0, 0, 0))
        _chunk(output, b"PLTE", PALETTE)
        output.write(struct.pack(">I", 2 + HEIGHT * (5 + size) + 4))
        output.write(b"IDAT")
        crc = binascii.crc32(b"IDAT")
        output.write(b"\x78\x01")
        crc = binascii.crc32(b"\x78\x01", crc)
        for y in range(HEIGHT):
            feed()
            header = struct.pack(
                "<BHH", 1 if y == HEIGHT - 1 else 0, size, size ^ 65535
            )
            output.write(header)
            crc = binascii.crc32(header, crc)
            output.write(b"\x00")
            crc = binascii.crc32(b"\x00", crc)
            adler_b += adler_a  # Filter byte = 0.
            row = view[y * row_bytes : (y + 1) * row_bytes]
            output.write(row)
            crc = binascii.crc32(row, crc)
            for value in row:
                adler_a += value
                adler_b += adler_a
            adler_a %= 65521
            adler_b %= 65521
        trailer = struct.pack(">I", (adler_b << 16) | adler_a)
        output.write(trailer)
        crc = binascii.crc32(trailer, crc)
        output.write(struct.pack(">I", crc & 0xFFFFFFFF))
        _chunk(output, b"IEND", b"")
    feed()


def _valid(value):
    return value is not None and math.isfinite(value)


def _draw(fb, history, now, feed):
    start = now - WINDOW
    limits = [[None, None], [None, None]]
    counts = [0, 0]
    for record in history.iter_records(now):
        feed()
        if not start <= record[0] <= now:
            continue
        for index in range(2):
            value = record[index + 1]
            if _valid(value):
                counts[index] += 1
                low, high = limits[index]
                limits[index][0] = value if low is None else min(low, value)
                limits[index][1] = value if high is None else max(high, value)

    fb.fill(0)
    fb.text("PICO - TEMPERATURE & HUMIDITY", 12, 7, 2)
    date = time.gmtime(now + 9 * 3600)
    fb.text(
        "7 DAYS / END %02d-%02d %02d:%02d JST" % (date[1], date[2], date[3], date[4]),
        12,
        21,
        6,
    )
    left, right = 48, 464
    tops, bottoms = (57, 165), (125, 233)
    for index in range(2):
        top, bottom = tops[index], bottoms[index]
        fb.text(("TEMPERATURE (C)", "HUMIDITY (%)")[index], 12, top - 15, 2)
        count_text = "%d readings" % counts[index]
        fb.text(count_text, right - len(count_text) * 8, top - 15, 6)
        fb.fill_rect(left, top, right - left + 1, bottom - top + 1, 1)
        low, high = limits[index]
        if low is None:
            low, high = (0, 40) if index == 0 else (0, 100)
        padding = max((high - low) * 0.12, 1 if index == 0 else 3)
        low, high = low - padding, high + padding
        limits[index] = (low, high)
        for step in range(3):
            y = top + (bottom - top) * step // 2
            fb.line(left, y, right, y, 3)
            label = "%.0f" % (high - (high - low) * step / 2)
            fb.text(label, left - len(label) * 8 - 6, y - 3, 6)
        if not counts[index]:
            fb.text("No readings", 204, top + 30, 6)

    previous = [None, None]
    for record in history.iter_records(now):
        feed()
        stamp = record[0]
        if not start <= stamp <= now:
            continue
        x = left + int((stamp - start) * (right - left) / WINDOW)
        for index in range(2):
            value = record[index + 1]
            if not _valid(value):
                previous[index] = None
                continue
            low, high = limits[index]
            top, bottom = tops[index], bottoms[index]
            y = bottom - int((value - low) * (bottom - top) / (high - low))
            y = max(top, min(bottom, y))
            color = 4 + index
            prev = previous[index]
            if prev is not None and 0 <= stamp - prev[0] <= GAP:
                fb.line(prev[1], prev[2], x, y, color)
            # An isolated observation remains visible.
            fb.fill_rect(
                max(left, x - 1),
                max(top, y - 1),
                min(3, right - max(left, x - 1) + 1),
                min(3, bottom - max(top, y - 1) + 1),
                color,
            )
            previous[index] = (stamp, x, y)

    for day in (0, 2, 4, 6, 7):
        stamp = start + day * 86400
        date = time.gmtime(stamp + 9 * 3600)
        x = left + day * (right - left) // 7
        label = "%02d/%02d" % (date[1], date[2])
        fb.text(label, min(WIDTH - 42, x - 20), 243, 6)
    fb.text("JST | Actual data only; gaps >15m break lines", 12, 261, 6)
    feed()


def render(history, now, path="climate.png", feed=lambda: None):
    """Write a seven-day PNG and return its path, releasing the 65,280B canvas."""
    gc.collect()
    feed()
    canvas = bytearray(WIDTH * HEIGHT // 2)
    fb = None
    try:
        fb = framebuf.FrameBuffer(canvas, WIDTH, HEIGHT, framebuf.GS4_HMSB)
        _draw(fb, history, now, feed)
        _write_png(canvas, path, feed)
    finally:
        fb = None
        canvas = None
        gc.collect()
    return path
