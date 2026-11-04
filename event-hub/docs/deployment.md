# Deploying BroncosHub

This is the setup for the real Flask app. The code and local tests are prepared; a hosted site, Google configuration, Stripe account setup, and a real Stripe sandbox walkthrough have not been verified by those tests.

## One Render service with persistent storage

The repository's `render.yaml` describes a paid Starter web service and a 1 GB persistent disk. Review Render's current price before creating it. Keeping this file in Git does not create a service.

Connect the team repository in Render and use its blueprint. It sets Python 3.12.13, root directory `event-hub`, build command `pip install -r requirements-production.txt`, start command `python tools/start_production.py`, and health check `/healthz`. The persistent paths are:

- Database: `sqlite:////var/data/broncoshub.db`.
- Covers: `/var/data/uploads/event-covers`.

The blueprint sets `APP_ENV=production`, a generated secret, and `TRUST_PROXY=true`. Payments start disabled; automatic Git deployments are off. Set `TRUSTED_HOSTS` to the real hostname only, such as `your-site.onrender.com`. Set `PUBLIC_BASE_URL` to its complete HTTPS origin, such as `https://your-site.onrender.com`, without a path or trailing slash. These are placeholders, not existing deployments.

Keep one web instance. The start script initializes the database, runs Gunicorn with one worker/four threads, and starts payment recovery on the same disk. If either child exits, both stop so the hosting service can restart them together. Do not run several independent SQLite instances or a separate worker with a different filesystem.

Production requires `APP_ENV=production`. Do not deploy the Flask debug server or either preview runner. `run.py` is for local development on port 5080; debug mode is off unless explicitly enabled. Preview shortcuts and fictional accounts never belong on the public site.

## Google and account roles

A team member must finish Google OAuth configuration before students can sign in. Add the actual HTTPS callback `https://YOUR-HOST/auth/google/callback` to that OAuth client. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` privately in Render; keep `ALLOWED_EMAIL_DOMAIN=scu.edu`.

Every verified SCU account can host events and manage only its own listings and statistics. New accounts remain students; no organizer-role assignment is needed. Paid events additionally require completed Stripe setup.

Admin moderation is separate. After a moderator signs in, a trusted operator can grant admin access in the hosting shell:

```sh
flask --app wsgi:app user-role moderator@scu.edu --role admin
```

The command displays the change and asks for confirmation. Use `--role student` to revoke admin access without removing the ability to host events. Existing organizer accounts retain hosting access. This command cannot create an account or bypass Google login.

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

Keep the database and covers together on persistent storage. Startup adds missing tables and applies additive SQLite upgrades without resetting records. Before schema changes, make a verified offline backup while both website and payment worker are stopped:

```sh
python tools/data_backup.py backup --database /var/data/broncoshub.db --covers /var/data/uploads/event-covers --output /var/data/broncoshub-backup.zip --app-stopped
```

Use a new backup filename each time and keep a private copy outside the service disk. The archive includes the database and referenced cover files, not secrets. Restore to a new directory, never over live files:

```sh
python tools/data_backup.py restore /path/to/broncoshub-backup.zip /path/to/new-restore
```

Verify restored data and images before switching the stopped service to those paths. **Restoring SQLite does not rewind Stripe.** Keep `PAYMENT_PROVIDER=disabled` while reviewing post-backup charges/refunds and planning reconciliation. Do not reopen sales from an old snapshot until those external transactions have been reconciled.

## Before live use

Run `python -m pytest -q tests tools` before deployment. Provider tests use local fake transport/provider implementations; they do not create Stripe accounts or charges.

On the hosted sandbox, verify Google sign-in, organizer onboarding, successful checkout and receipt, duplicate checkout, two buyers competing for the last seat, event cancellation/removal and completed refunds, delayed-webhook recovery, and a saved cover surviving restart. Compare the final states in BroncosHub and Stripe. Local passing tests do not establish deployment or live-payment success.

Give teammates the repository link and this document. Share access through the team's Render/Stripe/Google permissions, not by sending secret keys, `.env`, database exports, or payment details in chat.

Primary references checked November 4, 2026: [Stripe integration design](https://docs.stripe.com/connect/design-an-integration), [Accounts v2 configuration](https://docs.stripe.com/connect/accounts-v2/connected-account-configuration), [onboarding links](https://docs.stripe.com/api/v2/core/account-links/create), [direct charges](https://docs.stripe.com/connect/direct-charges), [webhooks](https://docs.stripe.com/webhooks), and [refund behavior](https://docs.stripe.com/refunds). Hosting commands/defaults come from this repository's `render.yaml`, `tools/start_production.py`, `tools/payment_worker.py`, and `gunicorn.conf.py`; review the actual hosting setup before applying them.
