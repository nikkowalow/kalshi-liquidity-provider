#!/bin/sh
# Start the bot on a server. Everything comes from environment variables:
#
#   KALSHI_PROD_KEY_ID        your API key id
#   KALSHI_PROD_PRIVATE_KEY   the private key itself (the PEM text, BEGIN...END lines included)
#   KLP_DASHBOARD_PASSWORD    the dashboard's password (required: it's served publicly)
#   KLP_CONFIG                config file (default config/prod.yaml)
#   KLP_DATA_DIR              persistent storage for runs/ and logs/ (default /data: mount a
#                             volume there, or the journal is lost on every redeploy)
#   KLP_LIVE                  "1" to trade real money (default: dry run, nothing sent)
#   PORT                      set by Railway: the dashboard listens on it
set -eu
cd /app

DATA="${KLP_DATA_DIR:-/data}"
mkdir -p "$DATA/runs" "$DATA/logs" "$DATA/secrets"
ln -sfn "$DATA/runs" runs
ln -sfn "$DATA/logs" logs
[ -w "$DATA/runs" ] || { echo "KLP_DATA_DIR $DATA is not writable: mount a volume there" >&2; exit 1; }

# Journal carried over from another machine (deploy/make_seed.sh): unpacked once, and only
# into a volume with no journal of its own, so it never overwrites the server's history.
SEED=deploy/seed/runs.tar.gz
if [ -f "$SEED" ]; then
  name="$(tar tzf "$SEED" 2>/dev/null | grep -v '^\._\|/\._' | head -1 | cut -d/ -f1)"
  if [ -n "$name" ] && [ ! -e "$DATA/runs/$name/state.json" ]; then
    tar xzf "$SEED" -C "$DATA/runs" --exclude='._*' 2>/dev/null
    echo "seeded the journal $name from $SEED"
  fi
fi

# The private keys come in as text; the bot reads them from a file.
for env in PROD DEMO; do
  eval "pem=\${KALSHI_${env}_PRIVATE_KEY:-}"
  if [ -n "$pem" ]; then
    file="$DATA/secrets/$(echo "$env" | tr 'A-Z' 'a-z').key"
    (umask 077 && printf '%s\n' "$pem" > "$file")
    export "KALSHI_${env}_PRIVATE_KEY_PATH=$file"
  fi
done

# A *_PATH copied from a laptop's .env points at a file that isn't here: say what to do.
for env in PROD DEMO; do
  eval "path=\${KALSHI_${env}_PRIVATE_KEY_PATH:-}"
  if [ -n "$path" ] && [ ! -f "$path" ]; then
    echo "KALSHI_${env}_PRIVATE_KEY_PATH=$path: no such file on this server." >&2
    echo "  Delete that variable and set KALSHI_${env}_PRIVATE_KEY to the key's text instead" >&2
    echo "  (the whole file, -----BEGIN ... to ...END-----)." >&2
    exit 1
  fi
done

export KLP_API_HOST="${KLP_API_HOST:-0.0.0.0}"
export KLP_API_PORT="${PORT:-${KLP_API_PORT:-8050}}"
CONFIG="${KLP_CONFIG:-config/prod.yaml}"

if [ "${KLP_LIVE:-0}" = "1" ]; then
  exec klp run -c "$CONFIG" --live --confirm-prod
else
  echo "KLP_LIVE is not 1: dry run (quotes are computed and shown, nothing is sent)"
  exec klp run -c "$CONFIG"
fi
