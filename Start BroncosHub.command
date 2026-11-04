#!/bin/zsh
set -e
cd -- "${0:A:h}/event-hub"
if [[ ! -x .venv/bin/python ]]; then
  echo 'First follow event-hub/README.md to install Python 3.12 and the app dependencies.'
  read -r '?Press Enter to close.'
  exit 1
fi
if ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3,12); import flask, stripe, PIL' >/dev/null 2>&1; then
  echo 'The app needs Python 3.12+ and the current requirements.txt. See event-hub/README.md.'
  read -r '?Press Enter to close.'
  exit 1
fi
export PORT=5080
if .venv/bin/python - <<'PY'
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:5080', timeout=2) as response:
        is_broncoshub = b'BroncosHub' in response.read(32768)
    raise SystemExit(0 if is_broncoshub else 1)
except (OSError, ValueError):
    raise SystemExit(1)
PY
then
  echo 'BroncosHub is already running at http://127.0.0.1:5080.'
  read -r '?Press Enter to close this extra window.'
  exit 0
fi
echo 'Open http://127.0.0.1:5080 for the actual BroncosHub app.'
echo 'Keep this window open. Press Control+C to stop.'
.venv/bin/python run.py
