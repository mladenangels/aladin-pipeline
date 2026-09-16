"""
One run of the pipeline: fetch the newest ALADIN nautical GRIB, build tiles, and
leave them in a directory ready to upload.

Called twice a day. DHMZ publishes the 00 and 12 UTC runs at about 02:50 and 14:50
UTC, so the schedule sits a little after each. If the file on the server has not
changed since the last successful run, this exits 0 without writing anything —
re-uploading identical tiles would be rude to a service that asked us to be
reasonable about downloads.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import urllib.request

import build_tiles

BASE = "https://radar.dhz.hr/~aladinhr/opendata/sailing"
FILE = "ALADIN-HR20_last_{run}_run_ADR.grb"


def fetch(run: str, user: str, password: str) -> bytes:
    url = f"{BASE}/{FILE.format(run=run)}"
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def main() -> int:
    user = os.environ.get("DHMZ_USER")
    password = os.environ.get("DHMZ_PASSWORD")
    if not user or not password:
        raise SystemExit("DHMZ_USER and DHMZ_PASSWORD must be set")

    run = os.environ.get("ALADIN_RUN", "00")
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "out"
    state_path = os.environ.get("ALADIN_STATE", ".aladin-state.json")

    print(f"fetching the {run} UTC run", flush=True)
    data = fetch(run, user, password)
    digest = hashlib.sha256(data).hexdigest()
    print(f"  {len(data)/1e6:.1f} MB, sha256 {digest[:16]}", flush=True)

    previous = {}
    if os.path.exists(state_path):
        previous = json.load(open(state_path))
    if previous.get("sha256") == digest:
        print("unchanged since the last run; nothing to publish")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".grb", delete=False) as handle:
        handle.write(data)
        grib_path = handle.name
    try:
        manifest = build_tiles.build(grib_path, out_dir)
    finally:
        os.remove(grib_path)

    total = sum(t["bytes"] for t in manifest["tiles"])
    print(f"built {len(manifest['tiles'])} tiles, {total/1e6:.1f} MB, run {manifest['run']}")

    with open(state_path, "w") as f:
        json.dump({"sha256": digest, "run": manifest["run"]}, f)
    # Anything after this is the uploader's job.
    print("::notice::published run " + manifest["run"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
