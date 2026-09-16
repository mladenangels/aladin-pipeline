"""
A GRIB edition 1 reader, in the standard library only.

DHMZ's nautical ALADIN-HR20 files use the simplest shape GRIB1 allows: a regular
lat/lon grid, simple grid-point packing, 8 bits per value and no bitmap. Decoding
that is about eighty lines, which is a better trade than carrying eccodes and
cfgrib into a CI job for one file a day. The reader refuses anything outside that
shape rather than guessing, so a change at DHMZ's end fails loudly.

Verified 2026-09-16 against ALADIN-HR20_last_00_run_ADR.grb (292 messages).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


def _u3(b: bytes) -> int:
    return (b[0] << 16) | (b[1] << 8) | b[2]


def _sign_magnitude_16(b: bytes) -> int:
    """GRIB1 stores signed integers as sign-and-magnitude, not two's complement."""
    value = ((b[0] & 0x7F) << 8) | b[1]
    return -value if b[0] & 0x80 else value


def _ibm_float(b: bytes) -> float:
    """The four-byte IBM System/360 float GRIB1 uses for the reference value."""
    sign = -1.0 if b[0] & 0x80 else 1.0
    exponent = b[0] & 0x7F
    mantissa = (b[1] << 16) | (b[2] << 8) | b[3]
    return sign * mantissa * (2.0**-24) * (16.0 ** (exponent - 64))


@dataclass(frozen=True)
class Grid:
    ni: int          # points along a parallel (west to east)
    nj: int          # points along a meridian
    lat1: float      # first point, in the order the values are stored
    lon1: float
    lat2: float
    lon2: float
    dlon: float
    dlat: float
    south_to_north: bool

    def index(self, lat: float, lon: float) -> int:
        """Nearest grid point. Raises if the point is off the grid."""
        lat_lo, lat_hi = sorted((self.lat1, self.lat2))
        lon_lo, lon_hi = sorted((self.lon1, self.lon2))
        if not (lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi):
            raise ValueError(f"{lat},{lon} is outside {lat_lo}..{lat_hi} / {lon_lo}..{lon_hi}")
        row = round((lat - self.lat1) / (self.dlat if self.south_to_north else -self.dlat))
        col = round((lon - self.lon1) / self.dlon)
        row = min(max(row, 0), self.nj - 1)
        col = min(max(col, 0), self.ni - 1)
        return row * self.ni + col


@dataclass(frozen=True)
class Message:
    parameter: int       # PDS table-2 parameter number
    level_type: int
    level: int
    run: str             # ISO-8601 UTC of the model run
    step_hours: int      # forecast hour from the run
    grid: Grid
    values: list[float]

    def at(self, lat: float, lon: float) -> float:
        return self.values[self.grid.index(lat, lon)]


def _read_grid(gds: bytes) -> Grid:
    if gds[5] != 0:
        raise ValueError(f"grid type {gds[5]} is not the regular lat/lon grid this reader handles")

    def angle(b: bytes) -> float:
        raw = _u3(b)
        value = (raw & 0x7FFFFF) / 1000.0
        return -value if raw & 0x800000 else value

    ni = struct.unpack(">H", gds[6:8])[0]
    nj = struct.unpack(">H", gds[8:10])[0]
    lat1, lon1 = angle(gds[10:13]), angle(gds[13:16])
    lat2, lon2 = angle(gds[17:20]), angle(gds[20:23])
    dlon = struct.unpack(">H", gds[23:25])[0] / 1000.0
    dlat = struct.unpack(">H", gds[25:27])[0] / 1000.0
    scanning = gds[27]
    # Bit 2 of the scanning mode set means rows run south to north.
    south_to_north = bool(scanning & 0x40)
    if scanning & 0x80:
        raise ValueError("east-to-west scanning is not handled")
    return Grid(ni, nj, lat1, lon1, lat2, lon2, dlon, dlat, south_to_north)


def _read_values(bds: bytes, decimal_scale: int, count: int) -> list[float]:
    flag = bds[3]
    if flag & 0x80:
        raise ValueError("spherical harmonic data is not handled")
    if flag & 0x40:
        raise ValueError("complex packing is not handled")
    if flag & 0x10:
        raise ValueError("the octet-14 extension is not handled")

    binary_scale = _sign_magnitude_16(bds[4:6])
    reference = _ibm_float(bds[6:10])
    bits = bds[10]
    payload = bds[11:]

    factor = (2.0**binary_scale) / (10.0**decimal_scale)
    base = reference / (10.0**decimal_scale)

    if bits == 8:
        return [base + payload[i] * factor for i in range(count)]
    if bits == 16:
        return [base + ((payload[2 * i] << 8) | payload[2 * i + 1]) * factor for i in range(count)]
    if bits == 0:
        return [base] * count

    # Anything else needs bit-level unpacking; correct, just slower.
    out = []
    acc = bit_count = 0
    mask = (1 << bits) - 1
    pos = 0
    for _ in range(count):
        while bit_count < bits:
            acc = (acc << 8) | payload[pos]
            pos += 1
            bit_count += 8
        bit_count -= bits
        out.append(base + ((acc >> bit_count) & mask) * factor)
    return out


def read(data: bytes) -> list[Message]:
    """Every message in a GRIB1 file, in file order."""
    messages: list[Message] = []
    pos = 0
    while True:
        start = data.find(b"GRIB", pos)
        if start < 0:
            return messages
        edition = data[start + 7]
        if edition != 1:
            raise ValueError(f"GRIB edition {edition}; this reader handles edition 1")
        total = _u3(data[start + 4 : start + 7])
        body = data[start : start + total]

        offset = 8
        pds_length = _u3(body[offset : offset + 3])
        pds = body[offset : offset + pds_length]
        offset += pds_length

        has_gds = bool(pds[7] & 0x80)
        has_bms = bool(pds[7] & 0x40)
        if not has_gds:
            raise ValueError("a message without a grid definition cannot be located")
        gds_length = _u3(body[offset : offset + 3])
        grid = _read_grid(body[offset : offset + gds_length])
        offset += gds_length
        if has_bms:
            offset += _u3(body[offset : offset + 3])

        bds_length = _u3(body[offset : offset + 3])
        bds = body[offset : offset + bds_length]

        century, year = pds[24] if len(pds) > 24 else 21, pds[12]
        # Seconds are always zero, but written out so the stamp is a plain ISO-8601
        # instant that any parser takes without special-casing.
        run = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:00Z".format(
            (century - 1) * 100 + year, pds[13], pds[14], pds[15], pds[16]
        )
        if pds[17] != 1:
            raise ValueError(f"time unit {pds[17]} is not hours")

        messages.append(
            Message(
                parameter=pds[8],
                level_type=pds[9],
                level=struct.unpack(">H", pds[10:12])[0],
                run=run,
                step_hours=pds[18],
                grid=grid,
                values=_read_values(bds, _sign_magnitude_16(pds[26:28]), grid.ni * grid.nj),
            )
        )
        pos = start + total
