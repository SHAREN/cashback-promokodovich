#!/usr/bin/env python3
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def png(size: int, path: Path) -> None:
    bg = (11, 13, 18, 255)
    purple = (104, 91, 255, 255)
    white = (246, 248, 255, 255)
    px = [list(bg) for _ in range(size * size)]

    def setp(x, y, color):
        if 0 <= x < size and 0 <= y < size:
            px[y * size + x] = list(color)

    cx = cy = size / 2
    radius = size * 0.34
    for y in range(size):
        for x in range(size):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                setp(x, y, purple)

    def disc(xc, yc, rr, color):
        for yy in range(int(yc - rr), int(yc + rr) + 1):
            for xx in range(int(xc - rr), int(xc + rr) + 1):
                if (xx - xc) ** 2 + (yy - yc) ** 2 <= rr ** 2:
                    setp(xx, yy, color)

    rr = size * 0.055
    disc(size * 0.41, size * 0.40, rr, white)
    disc(size * 0.59, size * 0.60, rr, white)

    x1, y1 = size * 0.43, size * 0.62
    x2, y2 = size * 0.57, size * 0.38
    width = size * 0.035
    vx, vy = x2 - x1, y2 - y1
    vv = vx * vx + vy * vy
    for y in range(size):
        for x in range(size):
            t = max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / vv))
            qx, qy = x1 + t * vx, y1 + t * vy
            if (x - qx) ** 2 + (y - qy) ** 2 <= width ** 2:
                setp(x, y, white)

    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            row.extend(px[y * size + x])
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw, 9))
    data += chunk(b"IEND", b"")
    path.write_bytes(data)

for size in (192, 512):
    png(size, ROOT / f"icon-{size}.png")
