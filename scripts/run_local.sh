#!/usr/bin/env bash
# Launch or open a verified loopback-only Ensora instance.
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-real}
python=${ENSORA_PYTHON:-/home/ensai/hackathon_ENSORA/.venv/bin/python}
case "$mode" in
  real) web=15180; api=18080 ;;
  demo) web=15181; api=18081 ;;
  *) printf 'Usage: bash scripts/run_local.sh [real|demo]\n' >&2; exit 2 ;;
esac

base="http://127.0.0.1:$web"
backend="http://127.0.0.1:$api"
expected="{\"mode\":\"$mode\"}"
read_mode() { curl -fsS --max-time 2 "$1/api/v1/runtime-mode" 2>/dev/null || true; }
open_instance() {
  printf 'ENSORA+ MODE %s %s\n' "${mode^^}" "$base"
  if [[ "$mode" == real && -n "${DISPLAY:-${WAYLAND_DISPLAY:-}}" ]] && command -v xdg-open >/dev/null; then
    xdg-open "$base" >/dev/null 2>&1 || true
  fi
}
existing=$(read_mode "$base")
if [[ "$existing" == "$expected" ]]; then
  open_instance
  exit 0
fi
if [[ -n "$existing" || -n "$(read_mode "$backend")" ]]; then
  printf 'The expected ENSORA+ frontend/backend pair is unavailable. Check ports %s and %s.\n' "$web" "$api" >&2
  exit 1
fi

export PYTHONPATH=backend:. VITE_API_BASE_URL= VITE_DEMO_MODE=false
unset DATABASE_URL ENSORA_P1_TOKEN_FILE
if [[ "$mode" == real ]]; then
  real_store=${ENSORA_REAL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/ensora-plus-real}
  if [[ ! -f "$real_store/sources/ensora.sqlite3" || ! -f "$real_store/runtime/ensora.db" ]]; then
    printf 'Set ENSORA_REAL_DIR to the prepared real-data copy. See README.md.\n' >&2
    exit 1
  fi
  unset DEMO_MODE ENSORA_DEMO_DIR
  export DATA_DIR="$real_store/runtime" LOCAL_DATA_DIR="$real_store/sources"
  export ENSORA_PAUSE_BACKGROUND_SYNC=true ENSAI_AUTO_LOGIN=false
else
  export DEMO_MODE=true
  ENSORA_DEMO_DIR=${ENSORA_DEMO_DIR:-$(mktemp -d /tmp/ensora-demo.XXXXXX)}
  export ENSORA_DEMO_DIR LOCAL_DATA_DIR="$ENSORA_DEMO_DIR/sources"
  if [[ ! -f "$ENSORA_DEMO_DIR/runtime/ensora.db" ]]; then
    "$python" scripts/seed_demo.py
  fi
fi

if [[ ! -x frontend/node_modules/.bin/vite ]]; then
  npm --prefix frontend ci
fi
"$python" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port "$api" --no-access-log &
backend_pid=$!
frontend_pid=""
trap 'kill "$backend_pid" ${frontend_pid:+"$frontend_pid"} 2>/dev/null || true' EXIT
(
  cd frontend
  export VITE_API_PROXY_TARGET="$backend"
  exec node_modules/.bin/vite --host 127.0.0.1 --port "$web" --strictPort --bypass-lock
) &
frontend_pid=$!
for attempt in {1..60}; do
  if [[ "$(read_mode "$base")" == "$expected" ]]; then
    curl -fsS --max-time 2 "$base/api/v1/sources/health" >/dev/null
    open_instance
    wait "$backend_pid" "$frontend_pid"
    exit
  fi
  if ! kill -0 "$backend_pid" "$frontend_pid" 2>/dev/null; then
    exit 1
  fi
  sleep 0.25
done
printf 'ENSORA+ did not become ready.\n' >&2
exit 1
