# Event Hub

A website where SCU students find campus and Santa Clara / San Jose events,
register, and buy tickets, and where organizers publish events and see
statistics. Python (Flask) backend, server-rendered HTML pages, SQLite database.

Team: Jaden Hill, Patrick Zhang, Ethan Can

## Run it locally

From the `event-hub` folder. On macOS, `python` is not a command until the virtual environment below is active.

```bash
cd event-hub
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and set `SECRET_KEY` to any long random string. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from the Google Auth Platform web client. Leave `ALLOWED_EMAIL_DOMAIN=scu.edu`. The client secret is shown only once; it belongs in `.env`, which is gitignored, not in `.env.example`.

In Google Cloud Console, the web client's authorized redirect URI must be exactly:

`http://127.0.0.1:5000/auth/google/callback`

Start the site and leave that Terminal window open:

```bash
python run.py
```

Open **http://127.0.0.1:5000/login** (use `127.0.0.1`, not `localhost`). Choose **Continue with Google** and sign in with a verified `@scu.edu` account. The first visit creates a student account; a later visit signs that person in. Press Control+C in the same Terminal window to stop the server.

```bash
pytest                             # run the tests
```

## Organizer dashboard

The real app now serves `/organizer/` and `/organizer/events/<id>/stats` for
organizer/admin accounts, limited to their own events. It counts confirmed
registrations, shows remaining capacity and groups signups by year. Revenue
stays unavailable until Payment records are implemented.

For a ready-to-use local demo, run `python tools/preview_organizer.py --port 5075`
and open **http://localhost:5075/demo**, or use **Start Organizer Demo.command**
in the repository root. This uses the real routes and models with fictional
records in a separate in-memory database. It does not seed, reset or sign into
the real application. See [integration notes](docs/ui-integration.md) for the
count definitions, tests and remaining dependencies.

## Event moderation

Signed-in users can report another organizer's published event at
`/events/<id>/report`. Admins review pending and resolved reports at
`/admin/reports`. Dismissing a report leaves the event unchanged; removing an
event marks it cancelled, cancels active registrations and resolves its pending
reports. Each decision records the admin, time and reason. These routes enforce
roles and CSRF checks; the report history is kept in the new `Report` table.

Try **http://localhost:5075/demo/admin** in the same isolated demo. The top bar
switches between organizer, admin and student reporting views. No real accounts
or event records are changed. The public event page still needs its reporting
link connected when Jaden's route is ready; payments/refunds remain unfinished.

## Where things go

```
event-hub/
├── run.py                 Start the dev server
├── config.py              Settings read from .env
├── requirements.txt       Python packages
├── app/
│   ├── __init__.py        create_app(): wires everything together
│   ├── extensions.py      Database + sign-in manager
│   ├── models/            User, Event, Registration, Report; Payment/Ticket unfinished
│   ├── routes/            URLs and page handlers, one file per area
│   │   ├── auth.py            sign up / sign in / profile        #1, #2, #22
│   │   ├── events.py          browse, search, details            UC-001
│   │   ├── registrations.py   register, pay, my registrations    UC-002 to UC-005
│   │   ├── organizer.py       create, edit, cancel, stats        UC-006 to UC-008
│   │   ├── reporting.py       submit event reports               #29
│   │   └── admin.py           review / dismiss / remove          #29
│   ├── services/          Business rules (registration, mock payments, stats)
│   ├── templates/         HTML pages (Jinja2), same folders as routes
│   └── static/            css/, js/, img/
├── tests/                 pytest files, one per area
└── scripts/seed.py        Demo data
```

Note: `app/` follows Flask's standard naming; to Flask, "app" means the web
server program. This project is a website.

Every function and template names the backlog item (#) and use case (UC-00X)
it implements. Search the code for `TODO` to see what's left.

## Demo accounts (after seeding)

| Email | Role | Password |
| --- | --- | --- |
| student@scu.edu | Student (Freshman) | password123 |
| organizer@scu.edu | Organizer | password123 |
| admin@scu.edu | Admin | password123 |

Demo payment cards: `4242…` succeeds, `4000…` is declined, `4999…` stays pending.

## Git workflow

- `main` always runs. Work on a branch per backlog item: `feature/09-register-free-event`.
- Open a pull request; one teammate reviews; tests must pass before merging.
