"""
Turn one ALADIN-HR20 nautical GRIB into small tiles the app can fetch one at a time.

Why tiles rather than an API: designer2.org is cPanel shared hosting. Serving static
files is the one thing it does reliably, so the shape of the data has to do the work
an endpoint would otherwise do. A half-degree tile at the model's native 2 km spacing
is about 120 KB, which is the same order as one Open-Meteo call, and the app fetches
exactly the one tile it is standing in.

Layout inside a tile is point-major: all 73 hourly steps for one grid point sit next
to each other. The app wants a time series for a spot, never a map of one hour, so
this is the ordering that makes a read a single seek.

Source: DHMZ ALADIN-HR20, CC BY 4.0. Attribution belongs in the app.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass

import grib1

# --- what we encode ------------------------------------------------------------

SPEED_STEP = 0.2      # m/s per unit, so one byte reaches 51 m/s (183 km/h)
DIR_STEP = 2.0        # degrees per unit
BYTES_PER_STEP = 3    # speed, direction, gust

TILE_DEGREES = 0.5
FORMAT_VERSION = 1

# Parameter numbers in DHMZ's GRIB table 1.
U_WIND, V_WIND = 33, 34
U_GUST, V_GUST = 196, 197   # verified against Open-Meteo: magnitude matched at step 0

# A coarse outline of the Adriatic, generous on the seaward side. It exists only to
# keep us from writing eight hundred tiles of Bosnian hinterland; a tile that clips
# the coast is cheap, a missing coastal tile is a hole in the app.
ADRIATIC = [
    (45.80, 13.00), (45.80, 13.90), (45.55, 14.60), (45.35, 14.80),
    (44.90, 15.30), (44.30, 15.60), (43.70, 16.50), (43.20, 17.30),
    (42.60, 18.20), (42.20, 18.80), (41.80, 19.40), (41.30, 19.60),
    (40.40, 19.40), (40.40, 18.20), (41.20, 16.20), (41.90, 15.50),
    (42.80, 14.20), (43.80, 13.30), (44.70, 12.20), (45.40, 12.20),
]


def _point_in_polygon(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _tile_touches_sea(lat0: float, lon0: float) -> bool:
    """True if any corner or the centre of the tile falls inside the outline."""
    for dlat in (0.0, TILE_DEGREES / 2, TILE_DEGREES):
        for dlon in (0.0, TILE_DEGREES / 2, TILE_DEGREES):
            if _point_in_polygon(lat0 + dlat, lon0 + dlon, ADRIATIC):
                return True
    return False


@dataclass(frozen=True)
class Field:
    """One parameter as a list of grids, indexed by forecast step."""
    steps: list[int]
    by_step: dict[int, grib1.Message]


def _collect(messages: list[grib1.Message]) -> dict[int, Field]:
    out: dict[int, dict[int, grib1.Message]] = {}
    for m in messages:
        out.setdefault(m.parameter, {})[m.step_hours] = m
    fields = {}
    for parameter, by_step in out.items():
        fields[parameter] = Field(sorted(by_step), by_step)
    return fields


def build(grib_path: str, out_dir: str) -> dict:
    messages = grib1.read(open(grib_path, "rb").read())
    fields = _collect(messages)

    for needed in (U_WIND, V_WIND, U_GUST, V_GUST):
        if needed not in fields:
            raise SystemExit(f"parameter {needed} is missing from {grib_path}")

    steps = fields[U_WIND].steps
    grid = fields[U_WIND].by_step[steps[0]].grid
    run = fields[U_WIND].by_step[steps[0]].run

    if fields[V_WIND].steps != steps or fields[U_GUST].steps != steps:
        raise SystemExit("the wind and gust fields do not cover the same forecast steps")

    os.makedirs(out_dir, exist_ok=True)

    lat_lo, lat_hi = sorted((grid.lat1, grid.lat2))
    lon_lo, lon_hi = sorted((grid.lon1, grid.lon2))

    tiles = []
    lat0 = math.floor(lat_lo / TILE_DEGREES) * TILE_DEGREES
    while lat0 < lat_hi:
        lon0 = math.floor(lon_lo / TILE_DEGREES) * TILE_DEGREES
        while lon0 < lon_hi:
            if _tile_touches_sea(lat0, lon0):
                written = _write_tile(fields, steps, grid, run, lat0, lon0, out_dir)
                if written:
                    tiles.append(written)
            lon0 = round(lon0 + TILE_DEGREES, 4)
        lat0 = round(lat0 + TILE_DEGREES, 4)

    manifest = {
        "format": FORMAT_VERSION,
        "model": "ALADIN-HR20",
        "source": "DHMZ",
        "licence": "CC BY 4.0",
        "attribution": "Izvor: DHMZ — ALADIN-HR",
        "run": run,
        "steps": steps,
        "tileDegrees": TILE_DEGREES,
        "speedStep": SPEED_STEP,
        "dirStep": DIR_STEP,
        "bytesPerStep": BYTES_PER_STEP,
        "tiles": tiles,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, separators=(",", ":"))
    return manifest


def tile_name(lat0: float, lon0: float) -> str:
    """South-west corner in hundredths of a degree, always signed and padded."""
    return "t{:+05d}{:+06d}.bin".format(round(lat0 * 100), round(lon0 * 100))


def _write_tile(fields, steps, grid, run, lat0, lon0, out_dir) -> dict | None:
    # Grid rows/columns that fall inside this tile.
    first_row = math.ceil((lat0 - grid.lat1) / grid.dlat)
    last_row = math.floor((lat0 + TILE_DEGREES - grid.lat1) / grid.dlat)
    first_col = math.ceil((lon0 - grid.lon1) / grid.dlon)
    last_col = math.floor((lon0 + TILE_DEGREES - grid.lon1) / grid.dlon)

    first_row = max(first_row, 0)
    first_col = max(first_col, 0)
    last_row = min(last_row, grid.nj - 1)
    last_col = min(last_col, grid.ni - 1)
    if last_row < first_row or last_col < first_col:
        return None

    nj = last_row - first_row + 1
    ni = last_col - first_col + 1

    u = [fields[U_WIND].by_step[s].values for s in steps]
    v = [fields[V_WIND].by_step[s].values for s in steps]
    gu = [fields[U_GUST].by_step[s].values for s in steps]
    gv = [fields[V_GUST].by_step[s].values for s in steps]

    body = bytearray(ni * nj * len(steps) * BYTES_PER_STEP)
    at = 0
    for row in range(first_row, last_row + 1):
        base = row * grid.ni
        for col in range(first_col, last_col + 1):
            cell = base + col
            for s in range(len(steps)):
                uu, vv = u[s][cell], v[s][cell]
                speed = math.hypot(uu, vv)
                # Meteorological direction: where the wind blows FROM.
                direction = (math.degrees(math.atan2(-uu, -vv)) + 360.0) % 360.0
                gust = math.hypot(gu[s][cell], gv[s][cell])
                body[at] = min(255, round(speed / SPEED_STEP))
                # 359° rounds to 180 units, which is 360°, which is 0° — wrap, do not clamp.
                body[at + 1] = round(direction / DIR_STEP) % 180
                body[at + 2] = min(255, round(gust / SPEED_STEP))
                at += 3

    path = os.path.join(out_dir, tile_name(lat0, lon0))
    with open(path, "wb") as f:
        f.write(body)

    return {
        "name": os.path.basename(path),
        "lat": round(grid.lat1 + first_row * grid.dlat, 6),
        "lon": round(grid.lon1 + first_col * grid.dlon, 6),
        "ni": ni,
        "nj": nj,
        "dlat": grid.dlat,
        "dlon": grid.dlon,
        "bytes": len(body),
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_tiles.py <aladin.grb> <out-dir>")
    m = build(sys.argv[1], sys.argv[2])
    total = sum(t["bytes"] for t in m["tiles"])
    print(f"run {m['run']}  {len(m['steps'])} steps")
    print(f"{len(m['tiles'])} tiles, {total/1e6:.1f} MB total, "
          f"{total/len(m['tiles'])/1024:.0f} KB average")
