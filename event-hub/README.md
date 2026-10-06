# Event Hub

A website where SCU students find campus and Santa Clara / San Jose events,
register, and buy tickets, and where organizers publish events and see
statistics. Python (Flask) backend, server-rendered HTML pages, SQLite database.

Team: Jaden Hill, Patrick Zhang, Ethan Can

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m scripts.seed             # demo users + events
python run.py                      # http://127.0.0.1:5000
pytest                             # run the tests
```

## Where things go

```
event-hub/
├── run.py                 Start the dev server
├── config.py              Settings read from .env
├── requirements.txt       Python packages
├── app/
│   ├── __init__.py        create_app(): wires everything together
│   ├── extensions.py      Database + sign-in manager
│   ├── models/            Database tables (User, Event, Registration, Payment, Ticket)
│   ├── routes/            URLs and page handlers, one file per area
│   │   ├── auth.py            sign up / sign in / profile        #1, #2, #22
│   │   ├── events.py          browse, search, details            UC-001
│   │   ├── registrations.py   register, pay, my registrations    UC-002 to UC-005
│   │   ├── organizer.py       create, edit, cancel, stats        UC-006 to UC-008
│   │   └── admin.py           moderation                         #29
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
