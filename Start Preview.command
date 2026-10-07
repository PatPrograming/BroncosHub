#!/bin/zsh
set -e
cd -- "${0:A:h}/event-hub"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'from importlib.metadata import version; assert tuple(map(int, version("Flask").split(".")[:3])) >= (3, 1, 3); assert tuple(map(int, version("Pillow").split(".")[:2])) >= (11, 3)' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r tools/requirements-preview.txt
fi
if .venv/bin/python - <<'PY'
import urllib.request
try:
    with urllib.request.urlopen('http://localhost:5074', timeout=2) as response:
        page = response.read(32768).decode()
    raise SystemExit(0 if 'BroncosHub' in page else 1)
except Exception:
    raise SystemExit(1)
PY
then
  echo 'BroncosHub is already running at http://localhost:5074'
  echo 'Open that address in your browser. You can close this window.'
  read -r '?Press Enter to close.'
else
  echo 'Open http://localhost:5074 to see BroncosHub.'
  echo 'Keep this window open while you explore. Press Control+C to stop.'
  .venv/bin/python tools/preview_ui.py --port 5074
fi
