#!/bin/sh
# Pack this machine's journal (runs/<journal>) to carry it over to the server, once.
#
#   deploy/make_seed.sh            # runs/prod-live -> deploy/seed/runs.tar.gz
#   railway up                     # the image carries it; the first start unpacks it
#
# Leaves out log lines (no longer used) and the dashboard's token/address files. The
# archive is kept out of git (.git/info/exclude) but not out of `railway up`.
# Stop the bot here first, so nothing is written while it's packed.
set -eu
cd "$(dirname "$0")/.."
NAME="${1:-prod-live}"
SRC="runs/$NAME"
[ -d "$SRC" ] || { echo "no journal at $SRC" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$NAME" deploy/seed
for f in state.json metrics.jsonl scan.json; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$TMP/$NAME/$f"
done
grep -v '^{"ts": [0-9.]*, "type": "log"' "$SRC/events.jsonl" > "$TMP/$NAME/events.jsonl" || true
# No macOS metadata (._ files, extended attributes): Linux tar doesn't want them.
COPYFILE_DISABLE=1 tar --no-xattrs -czf deploy/seed/runs.tar.gz -C "$TMP" "$NAME" 2>/dev/null \
  || COPYFILE_DISABLE=1 tar -czf deploy/seed/runs.tar.gz -C "$TMP" "$NAME"
grep -qx 'deploy/seed/' .git/info/exclude 2>/dev/null || echo 'deploy/seed/' >> .git/info/exclude
echo "deploy/seed/runs.tar.gz: $(du -h deploy/seed/runs.tar.gz | cut -f1) ($NAME)"
echo "Next: railway up. After the server's first start, delete deploy/seed/ (it is used once)."
