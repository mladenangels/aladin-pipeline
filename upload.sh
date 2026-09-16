#!/usr/bin/env bash
# Mirror the built tiles to designer2.org over FTPS.
#
# The manifest goes LAST and on its own. The app reads the manifest to learn which
# run is current and which tiles exist; if it went first, a phone could read a
# manifest promising tiles that were still uploading. Uploading it last means the
# worst case is a phone briefly seeing the previous run, which is correct, just old.
#
# Credentials come from the environment (GitHub Secrets in CI, ~/.netrc locally).
# STRICT rule for this host: only ever touch public_html/designer2/.

set -euo pipefail

OUT_DIR="${1:?usage: upload.sh <tile-dir>}"
REMOTE="public_html/designer2/spearfishing/aladin"
HOST="${FTP_HOST:-ftp.designer2.org}"

[ -f "$OUT_DIR/manifest.json" ] || { echo "no manifest in $OUT_DIR" >&2; exit 1; }

login=""
if [ -n "${FTP_USER:-}" ]; then
  login="-u ${FTP_USER},${FTP_PASSWORD}"
fi

# mirror --reverse --only-newer sends just what changed; --parallel keeps the
# ninety-odd small files from costing ninety round trips.
lftp $login "$HOST" <<LFTP
set ftp:ssl-allow yes
set ssl:verify-certificate no
set net:max-retries 3
set net:timeout 30
mkdir -p $REMOTE
cd $REMOTE
mirror --reverse --only-newer --parallel=4 --exclude-glob manifest.json --verbose=1 "$OUT_DIR" .
chmod 755 .
glob chmod 644 *.bin
put "$OUT_DIR/manifest.json" -o manifest.json
chmod 644 manifest.json
cls -l manifest.json
quit
LFTP

echo "checking the live manifest"
curl -fsS "https://designer2.org/designer2/spearfishing/aladin/manifest.json" \
  | python3 -c "import json,sys; m=json.load(sys.stdin); print('live run', m['run'], 'with', len(m['tiles']), 'tiles')"
