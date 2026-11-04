#!/bin/zsh
set -e
cd -- "${0:A:h}/event-hub"
if [[ ! -x .venv/bin/python ]]; then
  python3.12 -m venv .venv
fi
if ! .venv/bin/python -c 'import sys; assert sys.version_info >= (3, 12)' >/dev/null 2>&1; then
  echo 'This demo needs Python 3.12+. Follow event-hub/README.md to recreate the environment.'
  read -r '?Press Enter to close.'
  exit 1
fi
if ! .venv/bin/python -c 'import flask, flask_sqlalchemy, flask_login, authlib, dotenv, requests, stripe, PIL; from importlib.metadata import version; assert tuple(map(int, version("Flask").split(".")[:3])) >= (3, 1, 3); assert version("Pillow") == "13.0.0"; assert version("stripe") == "16.1.0"' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
echo 'Open http://localhost:5075/demo to try the organizer dashboard.'
echo 'Open http://localhost:5075/demo/admin to try event moderation.'
echo 'This demo uses fictional records and resets when stopped.'
echo 'Keep this window open. Press Control+C to stop.'
.venv/bin/python tools/preview_organizer.py --port 5075
