# BroncosHub

Campus events for the SCU community. Flask serves the pages, SQLAlchemy stores records in SQLite locally or PostgreSQL when hosted, and pytest checks the app. Team: Jaden Hill, Patrick Zhang, Ethan Can.

## Current application — November 6, 2026

The real app connects event creation, browsing/search/calendar, registration, tickets, profiles, persistent cover images, organizer statistics, and report moderation. Lists use pagination. Removed events and covers are hidden from students while receipts and administrator review history remain available. Registration capacity is checked inside database transactions so simultaneous requests cannot take the same final seat.

Organizer-owned Stripe payments are implemented using hosted onboarding and Checkout. BroncosHub takes no platform fee. Paid event publishing and checkout require a ready connected account. Signed payment notifications and reconciliation confirm purchases; returning from checkout alone never issues a ticket. Event cancellation/removal queues refunds and invalidates admission. Payment recovery runs alongside the production web process.

**Payments are disabled until configured. Google credentials and Stripe sandbox/live verification still need setup.** The deployment configuration uses Render Free with Supabase PostgreSQL and private image storage. A checked-in configuration and passing local tests do not prove that a public deployment or external sign-in/payments work. See [deployment and payment setup](docs/deployment.md).

The latest local production work adds Support and Privacy pages, signed-in organizer contact, shared database rate limits, registration/cancellation/change email notices, hosted backup tools, and optional redacted error alerts. These additions have not yet been deployed. Support is **ecan@scu.edu**; email delivery and Sentry alerts remain disabled until configured. [Gmail sender setup](docs/gmail-email.md) uses a separate send-only account authorization without changing student Google login. Hosted backup scheduling and external service delivery still need setup and verification.

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

Admin moderation remains separate. The selected initial admin is **ecan@scu.edu**, pending a verified Google sign-in. There is no automatic grant based on that email. After the account exists, a trusted operator can grant access in the intended database:

```sh
python -m flask --app wsgi:app user-role ecan@scu.edu --role admin
```

The command asks for confirmation. Use `--role student` to revoke admin access while keeping ordinary hosting access. Existing organizer accounts keep the same hosting permissions. The command cannot create accounts or bypass Google sign-in.

## Tests

```sh
python -m pytest -q tests tools
```

Tests cover accounts/roles, CSRF, events, capacity races, payments/refunds, images, profiles, calendar, moderation, support/contact privacy, request limits, notification retries, backups, and deployment settings. Stripe and email tests replace only the external provider boundary; actual app/database decisions still run. The Lab 5 class tests remain in `tests/lab5`; Lab 6 regression tests remain in `tests/lab6`.

The prepared GitHub Actions workflow (`.github/workflows/tests.yml` at the repository root) runs the suite with SQLite and PostgreSQL on pushes to `main` and pull requests. It has not yet run remotely; a local pass is not a GitHub Actions result.

## Organizer statistics and moderation

Organizers manage their own events and see confirmed sign-ups, available capacity, and year-level summaries. Counts represent registrations, not check-ins. Payment totals use saved successful amounts before fees and exclude failed, pending, refunded, and refund-pending payments. Stripe's Dashboard shows settlement, payouts, and processing fees.

Students can report published events. Administrators can dismiss a report or remove its event, with a recorded reason. Removal cancels active registrations, hides public content/covers, invalidates tickets, and queues Stripe refunds. Organizers cannot restore removed events. There is no attendee-kicking feature.

## Saved images and database upgrades

Local development defaults to `instance/broncoshub.db`. Covers are validated JPG/PNG/WebP images (up to 5 MB / 20 million pixels), re-encoded as WebP and saved to `instance/uploads/event-covers`. `COVER_UPLOAD_DIR` can select another local directory.

The hosted configuration uses PostgreSQL and a private Supabase Storage bucket instead. `REQUIRE_EXTERNAL_STORAGE=true` prevents it from silently saving data on Render's temporary filesystem. The media route checks event visibility and viewer access before serving any cover. Server-only storage credentials never go to the browser.

Startup adds the cover column to older SQLite databases, preserves records, and creates missing tables. It also distinguishes legacy admin removals from ordinary cancellations. PostgreSQL uses a separately provisioned private `broncoshub` schema. Startup does not migrate local data into Supabase. Before deployment/updates, follow the [hosted backup and restore procedure](docs/hosted-backups.md) or the [local backup procedure](docs/deployment.md#local-sqlite-backups), as appropriate. The hosted tool packages the database and referenced images; it does not schedule backups or configure an off-site destination. A database restore does not rewind payments already made in Stripe.

## Project structure

- `app/models/`: account/event/payment records, notification delivery records and shared rate-limit counters.
- `app/routes/`: page handlers and signed Stripe callbacks.
- `app/services/`: business rules, cover validation, payment gateway and reconciliation.
- `app/templates/` and `app/static/`: shared page UI, styles, scripts and artwork.
- `tools/start_production.py`: starts the production website and payment/notification worker together.
- `tools/data_backup.py`: offline local SQLite/local-image backup and restore.
- `tools/hosted_backup.py`: hosted database/image backup verification and isolated restore checks; see `docs/hosted-backups.md`.
- `docs/deployment.md`: hosting, secrets, payment setup and remaining external checks.
- `docs/ui-integration.md`: teammate handoff and UI integration notes.

## Older development previews

`tools/preview_ui.py` (5074) and `tools/preview_organizer.py` (5075) are isolated fictional-data previews retained for earlier labs. They are not the real app and must never be deployed. Their mock cards do not charge money. Run `run.py` on port 5080 for the actual product.

Before sharing changes, fetch teammate updates, review the diff, and run the relevant tests. Never commit `.env`, local databases, backups, or uploaded images.
