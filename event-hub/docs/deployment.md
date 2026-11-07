# Deploying BroncosHub

The real Flask app is deployed at **https://broncoshub.onrender.com**. The initial Render deployment and database health check succeeded on November 6, 2026. Google sign-in and Stripe sandbox/live payments remain unconfigured and unverified.

The support, request-limit, notification, optional alerting and backup-tool additions were deployed in commit `3ca5be5` on November 7, 2026. Public health, support and privacy checks passed. Gmail sender authorization completed, and one controlled local test reached the recipient's Spam folder. Hosted event-email flows still require real SCU login checks. No automatic backup schedule or live alert delivery has been configured or verified.

## Free Render website with Supabase storage

The repository's `render.yaml` uses one **Render Free** web service. Records live in **Supabase PostgreSQL**, and event covers live in a **private Supabase Storage bucket**. Render's local filesystem is temporary; the hosted app must not use it for records or uploads. The blueprint requires external storage and refuses to start with local SQLite or local covers.

The free Supabase project is `BroncosHub` (`fremmuavjhdmpdwdyoer`) in the existing personal organization. Its project URL is `https://fremmuavjhdmpdwdyoer.supabase.co`. The app uses the dedicated `broncoshub_app` database role and private `broncoshub` schema, not the Supabase anonymous browser role. App tables are not exposed through a public Data API schema. Flask checks the signed-in user's permissions before reading records or serving cover images.

Connect the team repository in Render and use its blueprint. It sets Python 3.12.13, root directory `event-hub`, build command `pip install -r requirements-production.txt`, start command `python tools/start_production.py`, and health check `/healthz`. Enter these values in Render's private environment settings:

| Variable | Setting |
| --- | --- |
| `DATABASE_URL` | PostgreSQL session-pooler connection URL containing the app role's password |
| `SUPABASE_URL` | `https://fremmuavjhdmpdwdyoer.supabase.co` |
| `SUPABASE_SECRET_KEY` | Server-only Supabase secret key for private image storage |

The database URL uses the `postgresql+psycopg` scheme, host `aws-0-us-west-1.pooler.supabase.com`, port `5432`, database `postgres`, and username `broncoshub_app.fremmuavjhdmpdwdyoer`. Keep the password out of Git, documents and messages. The app verifies the database server's TLS certificate using the bundled Supabase CA certificate. Do not disable certificate verification to fix a connection error.

The blueprint already sets `DATABASE_SCHEMA=broncoshub`, `COVER_STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=event-covers`, and `REQUIRE_EXTERNAL_STORAGE=true`. Provision the private schema and app role before starting a new database. The `event-covers` bucket must remain private, with a 5 MB file limit and `image/webp` allowed. The application validates JPG/PNG/WebP uploads and converts them to WebP before storage. Never expose the storage secret to browser code or make the bucket public to fix image access.

The blueprint also sets `APP_ENV=production`, a generated session secret, and `TRUST_PROXY=true`. Payments start disabled; automatic Git deployments are off. The app uses Render's assigned `RENDER_EXTERNAL_HOSTNAME` and matching HTTPS `RENDER_EXTERNAL_URL`, so the first deployment does not need a hostname entered in advance. It never derives trusted hosts from browser requests.

For a custom domain, explicitly set `TRUSTED_HOSTS` to the real hostname (or comma-separated hostnames) and `PUBLIC_BASE_URL` to its complete HTTPS origin, without a path or trailing slash. Explicit settings take precedence over Render's defaults. Do not replace them with a wildcard.

Keep one web instance for this setup. The start script initializes the database, runs Gunicorn with one worker/four threads, and starts the payment/notification worker. If either child exits, both stop so Render can restart them together. Database records and images remain outside the web process and survive its restarts and redeployments.

Production requires `APP_ENV=production`. Do not deploy the Flask debug server or either preview runner. `run.py` is for local development on port 5080; debug mode is off unless explicitly enabled. Preview shortcuts and fictional accounts never belong on the public site.

### Free-plan limits

As checked November 6, 2026, Render Free sleeps after 15 minutes without inbound traffic and has a shared allowance of 750 free instance hours per workspace each month. The first request after sleep can be slow. Free services do not provide a persistent disk or an interactive hosting shell. Do not use Render's free PostgreSQL service for this setup; it expires after 30 days.

Supabase Free includes a 500 MB database, 1 GB file storage and 5 GB egress. A project can pause after a week of inactivity, and free projects do not include automatic database backups. Stay within those limits and keep separate backups. Persistent external storage protects against a Render restart; it is not a backup or an always-on availability guarantee.

Keep `PAYMENT_PROVIDER=disabled` for the initial deployment. The payment and notification worker sleeps along with the free web service, so this configuration does not provide continuous payment recovery or prompt email delivery. Review hosting availability and complete the Stripe checks below before enabling real sales.

## Google and account roles

A team member must finish Google OAuth configuration before students can sign in. Patrick can use the origin `https://broncoshub.onrender.com` and the exact authorized redirect URI `https://broncoshub.onrender.com/auth/google/callback` for his web application OAuth client. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` privately in Render; keep `ALLOWED_EMAIL_DOMAIN=scu.edu`. These addresses are ready for configuration, but a real Google sign-in still needs testing after the credentials are added.

Every verified SCU account can host events and manage only its own listings and statistics. New accounts remain students; no organizer-role assignment is needed. Paid events additionally require completed Stripe setup.

Admin moderation is separate. **ecan@scu.edu** is the chosen initial admin, but access is pending that account's verified Google sign-in and an explicit role assignment. There is no automatic email-based promotion. After the account exists, a trusted operator can grant admin access from a local checkout connected to the hosted database. Render Free has no interactive shell. Install `requirements-production.txt` and load the production environment settings privately before running this command; otherwise the command could target the local database. Outside Render, set `TRUSTED_HOSTS` and `PUBLIC_BASE_URL` explicitly to the live site, and set `PYTHON_DOTENV_DISABLED=1` to avoid loading local development settings:

```sh
flask --app wsgi:app user-role ecan@scu.edu --role admin
```

The command displays the change and asks for confirmation. Use `--role student` to revoke admin access without removing the ability to host events. Existing organizer accounts retain hosting access. This command cannot create an account or bypass Google login.

## Support, request limits and event email

`SUPPORT_EMAIL=ecan@scu.edu` supplies the support contact and email reply address. The new `/support` and `/privacy` pages explain contact, refunds and saved information. An event's **Contact organizer** page requires a linked SCU account; it does not expose organizer emails to signed-out visitors. Removed-event receipts direct students to site support. Voluntary paid-ticket refund terms remain an organizer decision; the app does not promise automatic approval.

Keep `RATE_LIMIT_ENABLED=true` in production. Costly actions use shared SQLite/PostgreSQL counters, so changing browser sessions or web workers does not reset account limits. Limits cover event creation/edits/uploads, registrations, checkout, reports, profile changes and payment setup. Login starts use bounded hashed-address buckets. Ordinary browsing, health checks, Google callbacks and Stripe webhooks are exempt. Excess attempts receive HTTP 429 with `Retry-After`; current defaults are in `app/rate_limits.py`.

Free registrations, verified successful ticket payments, student cancellations, event time/location edits, organizer cancellations and moderation removals queue notices in the same transaction as the change. Pending or failed payments do not send confirmation. The worker sends committed notices separately and skips obsolete messages after cancellation, re-registration or another edit. An email already accepted by a provider cannot be recalled.

Email delivery defaults to **disabled** in application configuration. The chosen free sender, `broncoshubscu@gmail.com`, completed authorization on November 7; the actual runtime setting is managed privately in Render. This is separate from Patrick's student Google login and does not change its callback or permissions. See [Gmail sender setup](gmail-email.md), including the first test's Spam-folder result.

For Gmail, set these in Render's private environment settings after authorization:

| Variable | Setting |
| --- | --- |
| `NOTIFICATION_EMAIL_PROVIDER` | `gmail` |
| `NOTIFICATION_FROM_EMAIL` | The dedicated, authorized `@gmail.com` address |
| `GMAIL_CLIENT_ID` | Sender Desktop OAuth client ID |
| `GMAIL_CLIENT_SECRET` | Sender OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | Sender's send-only authorization |
| `SUPPORT_EMAIL` | `ecan@scu.edu` (reply address) |
| `PUBLIC_BASE_URL` | Trusted public HTTPS origin |

Gmail does not provide Resend-style idempotency. A timeout, ambiguous send response or recovered in-flight send is recorded as `delivery_unknown` for operator review instead of blindly sending again. A deterministic Message-ID helps the operator find a message but does not guarantee deduplication. Explicitly rejected requests can retry with bounded backoff. Do not reset unknown deliveries or switch providers to replay them; first check the sender's Sent folder and the recipient with their cooperation. The app has no permission to read the mailbox.

Retries stop after eight attempts or a 23-hour notice lifetime; terminal records discard message content and are removed after 30 days. Daily quota delays or a sleeping Render service can therefore cause a notice to expire. A disabled provider still expires/prunes old records without sending them. A `sent` record means the provider accepted the request, not proof that the message reached the recipient's inbox.

Resend remains an alternative when the team owns and verifies a sending domain. It uses the same stored payload and idempotency key on retries, with a lifetime shorter than Resend's 24-hour idempotency window:

| Variable | Setting |
| --- | --- |
| `NOTIFICATION_EMAIL_PROVIDER` | `resend` |
| `NOTIFICATION_FROM_EMAIL` | Verified sender address, without a display name |
| `RESEND_API_KEY` | Resend sending API key |
| `SUPPORT_EMAIL` | `ecan@scu.edu` |
| `PUBLIC_BASE_URL` | Trusted public HTTPS origin |

The SCU support inbox is a reply address, not authorization to send on behalf of `scu.edu`. Gmail's personal-account daily limit is generally 500 messages; Resend Free is 100 per day and 3,000 per month. These are limits, not guaranteed capacity or inbox delivery. Test registration, student cancellation, rescheduling, organizer cancellation and removal using controlled accounts, including replies reaching support. Automated tests replace the provider; they do not establish real delivery.

## Optional alerts

`SENTRY_DSN` is empty by default. A configured Sentry project can receive fixed failure categories for server/database errors, payment recovery, failed event notifications and refunds needing attention. The integration rebuilds events from an allowlist: no student email, event text, request body, URL, cookies, stack trace or provider response is included. It does not enable automatic request, exception, tracing or session capture. Reports are limited per category/process; queuing an alert does not prove delivery.

Configure Sentry's notification rules and the alert recipient separately, then test a controlled failure. No live Sentry delivery or dashboard rule has been verified. `/healthz` remains a database health check; this optional integration is not an external uptime monitor.

## Stripe: organizers receive their own payments

BroncosHub uses Accounts v2 with a full Stripe Dashboard, hosted onboarding, and direct charges. Payments go to the organizer's connected account. BroncosHub adds no application/platform fee; Stripe processing fees still apply.

A team member needs a Stripe platform account with Connect and Accounts v2 enabled. Finish Stripe's platform activation and onboarding branding requirements in its Dashboard. Do not invent a business identity, accept agreements for another organizer, or share API keys in Git or a document.

Configure a separate Stripe sandbox first:

| Variable | Value |
| --- | --- |
| `PAYMENT_PROVIDER` | `stripe` |
| `STRIPE_SECRET_KEY` | The platform's sandbox `sk_test_...` key |
| `STRIPE_WEBHOOK_SECRET` | The signing secret for this endpoint |
| `STRIPE_LIVE_MODE` | `false` |
| `PUBLIC_BASE_URL` | The site's trusted HTTPS origin |

Set up `https://YOUR-HOST/billing/webhook` as a Connect event destination receiving **snapshot events for connected accounts**, with API version **2026-10-28.endive**. Subscribe to:

- `checkout.session.completed`
- `checkout.session.expired`
- `charge.refunded`
- `refund.created`
- `refund.updated`
- `refund.failed`

Save that endpoint's `whsec_...` secret in the hosting environment. A platform-only webhook misses organizer direct charges; a thin Accounts v2 event destination does not replace these payment snapshot events.

An organizer opens **Organize → Event payments**, explicitly selects **United States** as the payment account's location, and continues to Stripe. This first release supports US-based accounts only; the choice is not citizenship. For club events, its authorized treasurer/account owner should complete setup. Stripe collects the remaining legal and bank details.

The account uses a full Dashboard and `fees_collector=stripe` / `losses_collector=stripe`. Paid checkout stays closed until an API refresh confirms active card payments and payouts, completed required details, and matching test/live mode. Returning from onboarding does not prove approval. Each checkout checks the account's current capabilities again.

For real sales, use a separate clean live deployment/database, the platform's `sk_live_...` key, a live connected-account webhook endpoint/secret, and `STRIPE_LIVE_MODE=true`. Organizers must finish live onboarding. Sandbox account IDs and approvals are not live credentials. Keep payments disabled until the configuration and sandbox flow below have been checked.

## Checkout, refunds, and recovery

Paid tickets must be at least $0.50. The server fixes the price and holds a seat for 31 minutes before opening Stripe-hosted Checkout. BroncosHub does not receive card numbers. A success-page redirect does not confirm a purchase: signed webhook processing or authenticated reconciliation verifies the account, payment, amount, currency, and reservation before issuing a ticket.

Duplicate requests reuse the same attempt. Capacity includes active checkout holds. A late payment for an expired hold or closed event does not issue admission and enters the refund workflow.

Organizer cancellation or moderation removal closes registrations, invalidates tickets, and queues refunds for real Stripe purchases. A refund can stay pending, including when the organizer has insufficient Stripe balance. It is not shown as complete just because it was requested. Stripe's original processing fees generally are not returned. Failed refunds require operator attention in Stripe; do not promise a completion date.

There is no feature to kick individual attendees and no attendee self-service paid cancellation/refund button. An attendee can contact the organizer, whose Stripe Dashboard supports refunds. Partial external refunds invalidate admission and enter full-refund reconciliation.

The recovery process checks up to 20 pending attempts and waits 30 seconds before its next cycle. It retries missed confirmations, expired checkouts, and pending refunds. Run a bounded manual pass in the same environment/database with:

```sh
flask --app wsgi:app billing reconcile --limit 100
```

Check hosting logs when recovery fails. Unresolved entries rotate through the queue. Refund-history retrieval stops after 20 additional pages; an unverified history stays unresolved for operator review rather than falsely becoming a completed refund.

## Data and updates

For the hosted app, the PostgreSQL database and private Storage bucket are the source of saved data. Startup creates missing app tables inside the already provisioned private schema. It does not move a local SQLite database or upload existing local covers, and it is not a general schema-migration system. Review and back up data before future schema changes.

### Hosted PostgreSQL and Storage backups

Follow [Hosted backups and restore checks](hosted-backups.md) for `tools/hosted_backup.py`. It packages the private app schema and every referenced cover, verifies the archive, and checks a restore in a separate local database. A database dump alone does **not** contain images. Stop writes and both workers as described in that procedure; the tool's `--app-stopped` flag does not stop them for you.

This is operator-run tooling. A private off-site destination, recurring schedule, retention policy, failure alerts and a verified restore drill still need setup. Keep archives out of Git and group messages. `tools/data_backup.py` remains for local SQLite/local images only.

### Local SQLite backups

Local development still defaults to `instance/broncoshub.db` and `instance/uploads/event-covers`. Startup applies the existing additive SQLite upgrades without resetting records. Stop both the local website and payment worker before making a backup:

```sh
python tools/data_backup.py backup --database instance/broncoshub.db --covers instance/uploads/event-covers --output /private/backup/location/broncoshub-local.zip --app-stopped
```

Use a new filename each time. The archive includes the database and referenced local cover files, not secrets. Restore to a new directory, never over live files:

```sh
python tools/data_backup.py restore /path/to/broncoshub-local.zip /path/to/new-restore
```

Verify the restored database and images before switching the stopped local app to those paths.

**Restoring any database does not rewind Stripe.** Keep payments disabled while reviewing post-backup charges/refunds and planning reconciliation. Do not reopen sales from an old snapshot until those external transactions have been reconciled.

## Before live use

Run `python -m pytest -q tests tools` before deployment. Provider tests use local fake transport/provider implementations; they do not create Stripe accounts or charges.

The prepared `.github/workflows/tests.yml` runs SQLite and PostgreSQL tests on `main` pushes, pull requests and manual dispatch. It has not yet run on GitHub; verify its remote result after pushing. It neither deploys the app nor configures backups, email or alerts.

On the hosted sandbox, verify Google sign-in, organizer onboarding, successful checkout and receipt, duplicate checkout, two buyers competing for the last seat, event cancellation/removal and completed refunds, delayed-webhook recovery, and a saved cover surviving restart. Compare the final states in BroncosHub and Stripe. Local passing tests do not establish deployment or live-payment success.

Give teammates the repository link and this document. Share access through the team's Render/Stripe/Google permissions, not by sending secret keys, `.env`, database exports, or payment details in chat.

Primary references checked November 4, 2026: [Stripe integration design](https://docs.stripe.com/connect/design-an-integration), [Accounts v2 configuration](https://docs.stripe.com/connect/accounts-v2/connected-account-configuration), [onboarding links](https://docs.stripe.com/api/v2/core/account-links/create), [direct charges](https://docs.stripe.com/connect/direct-charges), [webhooks](https://docs.stripe.com/webhooks), and [refund behavior](https://docs.stripe.com/refunds). Hosting commands/defaults come from this repository's `render.yaml`, `tools/start_production.py`, `tools/payment_worker.py`, and `gunicorn.conf.py`; review the actual hosting setup before applying them.

Hosting references checked November 6, 2026: [Render Free limits](https://render.com/docs/free), [Blueprint fields](https://render.com/docs/blueprint-spec), [default environment variables](https://render.com/docs/environment-variables), [Supabase pricing and free limits](https://supabase.com/pricing), [PostgreSQL connections](https://supabase.com/docs/guides/database/connecting-to-postgres), and [private Storage buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals).

Email references checked November 6, 2026: [Resend idempotency keys](https://resend.com/docs/dashboard/emails/idempotency-keys) and [send-email API](https://resend.com/docs/api-reference/emails/send-email). Configuration, retention and alerting descriptions above were checked against the current local source; they do not establish hosted configuration or delivery.
