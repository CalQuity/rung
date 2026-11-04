#!/usr/bin/env bash
# Start the API (port $API_PORT, default 8000) and the web app (port $PORT, default 3000).
# The database at $DATABASE_PATH (default backend/acme.db) is recreated and seeded on every start.
set -euo pipefail
cd "$(dirname "$0")"

API_PORT="${API_PORT:-8000}"
PORT="${PORT:-3000}"
DATABASE_PATH="${DATABASE_PATH:-$PWD/backend/acme.db}"
export API_PORT PORT DATABASE_PATH

rm -f "$DATABASE_PATH" "$DATABASE_PATH-wal" "$DATABASE_PATH-shm"
[ -d frontend/node_modules ] || (cd frontend && npm ci --no-audit --no-fund)

trap 'kill 0' EXIT INT TERM

(cd backend && exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT") &
api=$!
(cd frontend && exec npx next dev --port "$PORT") &
web=$!

echo "Acme Store: http://localhost:$PORT  (API on http://127.0.0.1:$API_PORT)"
# Stop both as soon as either exits (a port in use, a crash).
while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do sleep 1; done
