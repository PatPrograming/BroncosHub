#!/bin/zsh
set -e
cd -- "${0:A:h}/event-hub"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import flask, flask_sqlalchemy, flask_login, authlib, dotenv, requests' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
echo 'Open http://localhost:5075/demo to try the organizer dashboard.'
echo 'Open http://localhost:5075/demo/admin to try event moderation.'
echo 'This demo uses fictional records and resets when stopped.'
echo 'Keep this window open. Press Control+C to stop.'
.venv/bin/python tools/preview_organizer.py --port 5075
