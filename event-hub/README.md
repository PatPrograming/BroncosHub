# BroncosHub

Campus events for the SCU community. Flask serves the pages, SQLAlchemy stores records in SQLite, and pytest checks the app. Team: Jaden Hill, Patrick Zhang, Ethan Can.

## Current application — November 4, 2026

The real app connects event creation, browsing/search/calendar, registration, tickets, profiles, persistent cover images, organizer statistics, and report moderation. Lists use pagination. Removed events and covers are hidden from students while receipts and administrator review history remain available. Registration capacity is checked inside database transactions so simultaneous requests cannot take the same final seat.

Organizer-owned Stripe payments are implemented using hosted onboarding and Checkout. BroncosHub takes no platform fee. Paid event publishing and checkout require a ready connected account. Signed payment notifications and reconciliation confirm purchases; returning from checkout alone never issues a ticket. Event cancellation/removal queues refunds and invalidates admission. Payment recovery runs alongside the production web process.

**Payments are disabled until configured. Google credentials, Stripe sandbox/live verification, and public hosting still need setup.** Local tests do not prove these external services work. See [deployment and payment setup](docs/deployment.md).

## Run it locally

Use **Python 3.12 or newer**. From the repository root:

```sh
cd event-hub
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate`. If `.env` does not exist, copy `.env.example` to `.env`. Keep an existing `.env`; do not overwrite its settings. Set `SECRET_KEY` to a long random value. Keep `.env` out of Git.

```sh
python run.py
```

Open **http://127.0.0.1:5080**. This is the actual application and saved database, with no fictional accounts. Stop it with Control+C. Debug mode is off by default. `PORT` can change the port; the Google callback must match the chosen address exactly.

Google sign-in needs `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`, with `ALLOWED_EMAIL_DOMAIN=scu.edu`. For the default local address, the authorized redirect URI is:

`http://127.0.0.1:5080/auth/google/callback`

Every verified SCU account can create events and manage its own events, statistics and payment setup. New accounts keep the student role; no organizer approval is needed. Publishing a paid event requires the host’s Stripe account to be ready.

Admin moderation remains separate. After a moderator signs in, a trusted operator can grant admin access:

```sh
python -m flask --app wsgi:app user-role moderator@scu.edu --role admin
```

The command asks for confirmation. Use `--role student` to revoke admin access while keeping ordinary hosting access. Existing organizer accounts keep the same hosting permissions. The command cannot create accounts or bypass Google sign-in.

## Tests

```sh
python -m pytest -q tests tools
```

Tests cover accounts/roles, CSRF, events, capacity races, payments/refunds, images, profiles, calendar, moderation, backups, and deployment settings. Stripe tests replace only the external provider boundary; actual app/database decisions still run. The Lab 5 class tests remain in `tests/lab5`; Lab 6 regression tests remain in `tests/lab6`.

## Organizer statistics and moderation

Organizers manage their own events and see confirmed sign-ups, available capacity, and year-level summaries. Counts represent registrations, not check-ins. Payment totals use saved successful amounts before fees and exclude failed, pending, refunded, and refund-pending payments. Stripe's Dashboard shows settlement, payouts, and processing fees.

Students can report published events. Administrators can dismiss a report or remove its event, with a recorded reason. Removal cancels active registrations, hides public content/covers, invalidates tickets, and queues Stripe refunds. Organizers cannot restore removed events. There is no attendee-kicking feature.

## Saved images and database upgrades

The default database is `instance/broncoshub.db`. Covers are validated JPG/PNG/WebP images (up to 5 MB / 20 million pixels), re-encoded as WebP and saved to `instance/uploads/event-covers`. `COVER_UPLOAD_DIR` can select another directory. The media route checks access before serving private or removed event images.

Startup adds the cover column to older SQLite databases, preserves records, and creates new payment tables. It also distinguishes legacy admin removals from ordinary cancellations. Keep the database and images together on persistent storage. Before deployment/updates, follow the verified offline backup and restore commands in [deployment notes](docs/deployment.md#data-and-updates). A database restore does not rewind payments already made in Stripe.

## Project structure

- `app/models/`: users, events, registrations, payments, tickets, reports, connected accounts and checkout attempts.
- `app/routes/`: page handlers and signed Stripe callbacks.
- `app/services/`: business rules, cover validation, payment gateway and reconciliation.
- `app/templates/` and `app/static/`: shared page UI, styles, scripts and artwork.
- `tools/start_production.py`: starts the production website and payment recovery together.
- `tools/data_backup.py`: offline database/image backup and restore.
- `docs/deployment.md`: hosting, secrets, payment setup and remaining external checks.
- `docs/ui-integration.md`: teammate handoff and UI integration notes.

## Older development previews

`tools/preview_ui.py` (5074) and `tools/preview_organizer.py` (5075) are isolated fictional-data previews retained for earlier labs. They are not the real app and must never be deployed. Their mock cards do not charge money. Run `run.py` on port 5080 for the actual product.

Before sharing changes, fetch teammate updates, review the diff, and run the relevant tests. Never commit `.env`, local databases, backups, or uploaded images.
