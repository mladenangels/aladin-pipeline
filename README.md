# ALADIN wind tiles for Tisch Spearfishing

Mirrors DHMZ's ALADIN-HR20 nautical wind forecast to designer2.org as small tiles
the app can fetch one at a time.

DHMZ granted access on 2026-09-16 and explicitly approved this arrangement: we pull
the file, we serve the processed result, their server sees two downloads a day.

## Why this exists

Open-Meteo's global model over the Adriatic sits on a grid several kilometres wide.
It cannot see that the bora comes down one channel and not the next, which is the
difference between a dive and a wasted drive. ALADIN-HR20 is run by the Croatian met
service at 2 km over exactly this coast.

The app cannot read DHMZ directly: the files are 34 MB of whole-domain GRIB behind a
password, and a password does not belong in an APK.

## What it does

1. Fetch `ALADIN-HR20_last_{00,12}_run_ADR.grb` (~34 MB, the whole Adriatic).
2. Skip everything if the file's hash has not changed since the last publish.
3. Decode it — GRIB edition 1, no libraries, see `grib1.py`.
4. Write half-degree tiles at the model's native 2 km spacing, plus a manifest.
5. Mirror them to designer2.org over FTPS, manifest last.

95 tiles cover the Adriatic. 11 MB in total, 115 KB each; the app fetches one.

## Running it by hand

```bash
DHMZ_USER=... DHMZ_PASSWORD=... ALADIN_RUN=00 python3 run.py out
./upload.sh out
```

FTPS credentials come from `~/.netrc` locally, from Secrets in CI.

## Secrets the workflow needs

`DHMZ_USER`, `DHMZ_PASSWORD`, `FTP_HOST`, `FTP_USER`, `FTP_PASSWORD`.

## Tile format

A tile is point-major: all 73 hourly steps for one grid point sit next to each other,
because the app wants a time series for a spot and never a map of one hour. Three
bytes per step — wind speed in 0.2 m/s, direction in 2°, gust in 0.2 m/s. Round-trip
error measured against the GRIB is 0.097 m/s and 1°, which is the quantisation and
nothing more.

`manifest.json` carries the run, the steps, the scales and every tile's corner and
shape. The reader in the app is `data/remote/AladinTiles.kt`.

## Licence and attribution

The data is DHMZ's, under CC BY 4.0. Anything showing these numbers must say
**"Izvor: DHMZ — ALADIN-HR"** with the model run. The app does, on the conditions
screen and in settings.
