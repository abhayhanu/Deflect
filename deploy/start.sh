#!/usr/bin/env bash
# Starts everything the demo needs inside one container, then the API.
# Postgres and Qdrant are started here unless you point at your own with
# DATABASE_URL or POSTGRES_HOST, and QDRANT_URL. Nothing in here is kept
# between restarts, which is what a demo wants: every start is a clean shop.

set -euo pipefail
cd "$(dirname "$0")/.."

export DEFLECT_DEMO="${DEFLECT_DEMO:-1}"
export DEFLECT_CLOCK=seed_anchor
export DEFLECT_TRUST_PROXY="${DEFLECT_TRUST_PROXY:-1}"
export DEFLECT_SEED_ANCHOR="${DEFLECT_SEED_ANCHOR:-2026-10-01T10:00:00+00:00}"
# The setup the eval numbers were measured on. Change these in the host's settings, not here.
export DEFLECT_PROVIDER="${DEFLECT_PROVIDER:-gemini}"
export DEFLECT_CLASSIFIER_PROVIDER="${DEFLECT_CLASSIFIER_PROVIDER:-jev}"
export DEFLECT_CHECKER_PROVIDER="${DEFLECT_CHECKER_PROVIDER:-jev}"
PORT="${PORT:-7860}"

wait_for() {
  python - "$1" "$2" <<'PY'
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
for _ in range(120):
    try:
        socket.create_connection((host, port), timeout=1).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.5)
sys.exit(f"nothing answered on {host}:{port} after a minute")
PY
}

if [ -z "${DATABASE_URL:-}" ] && [ -z "${POSTGRES_HOST:-}" ]; then
  PG_BIN="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"
  PGDATA="${PGDATA:-$HOME/pgdata}"
  if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "deflect" > "$HOME/.pgpass_seed"
    "$PG_BIN/initdb" -D "$PGDATA" -U deflect --pwfile="$HOME/.pgpass_seed" --auth=scram-sha-256 > /dev/null
    rm -f "$HOME/.pgpass_seed"
  fi
  # Only this container can reach it. The port is the one the project's defaults expect.
  "$PG_BIN/pg_ctl" -D "$PGDATA" -w -l "$HOME/postgres.log" \
    -o "-c listen_addresses=127.0.0.1 -c port=5434 -c unix_socket_directories=/tmp" start
  PGPASSWORD=deflect "$PG_BIN/createdb" -h 127.0.0.1 -p 5434 -U deflect deflect 2> /dev/null || true
  echo "Postgres is up."
fi

if [ -z "${QDRANT_URL:-}" ]; then
  (cd /qdrant && QDRANT__TELEMETRY_DISABLED=true QDRANT__SERVICE__HOST=127.0.0.1 ./qdrant > "$HOME/qdrant.log" 2>&1 &)
  wait_for 127.0.0.1 6333
  echo "Qdrant is up."
fi

# Seeding without a reset only fills in what is missing, so a database you brought keeps its tickets.
python -m data.seed_orders --anchor "$DEFLECT_SEED_ANCHOR" > /dev/null
python -m data.index_policies
echo "Seeded and indexed. Starting the API on port $PORT."

exec python -m uvicorn api.main:app --host 0.0.0.0 --port "$PORT"
