# EventHub
Patrick Jaden Ethan CSEN 174

## Open the login page

From this folder:

```bash
cd event-hub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `event-hub/.env`: any long random `SECRET_KEY`, plus the Google web client's `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Keep `ALLOWED_EMAIL_DOMAIN=scu.edu`. The Google redirect URI is `http://127.0.0.1:5000/auth/google/callback`.

```bash
python run.py
```

Leave that Terminal window open, then go to **http://127.0.0.1:5000/login** and choose **Continue with Google**. Press Control+C to stop the server.
