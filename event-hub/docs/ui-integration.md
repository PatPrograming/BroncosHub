# BroncosHub UI integration

## Current status — November 4, 2026

The real Flask application connects Google authentication, public event browsing,
event creation/editing/cancellation, registrations, Stripe-hosted checkout/tickets,
organizer statistics and event moderation. Payments default to disabled until the
platform and each organizer finish setup. Stripe integration has automated local
tests; real sandbox onboarding and charges still need verification. The templates,
shared styles, JavaScript and local artwork are
used by those routes.
Profile counts and the next plan now use saved registrations, the calendar uses
current Pacific dates, and uploaded event covers persist on disk. Admin removal
now hides the public listing and media while preserving authorized review history.

Use `run.py` for the actual application with Google sign-in and saved records.
`tools/preview_organizer.py` on port 5075 is an isolated development preview with
fictional accounts. `tools/preview_ui.py` on port 5074 is
the older UI-only sample walkthrough; it is not evidence that its sample actions
have real backend support. The sections below distinguish those two runners.

## Organizer dashboard and statistics

- `GET /organizer/` lists only the signed-in organizer's events, with current and
  upcoming events first, then historical/cancelled events. Admins also see only
  their own events in this section. Every verified SCU user can host, including
  accounts with the student role. Guests go to login; accounts without a linked
  SCU identity receive 403. Unavailable or other owners' event statistics return 404.
- `GET /organizer/events/<id>/stats` shows confirmed registration counts and year
  totals. Pending, failed and cancelled registrations are excluded. Missing or
  unrecognized year levels are Unknown. The denominator is all confirmed signups
  for that event; rounded percentages may not sum to exactly 100%.
- Dashboard totals cover all owned events. Published event count includes past
  and upcoming published events; confirmed signup count includes historical
  records on cancelled events if any remain. These are signups, not check-ins.
- Capacity and remaining spaces use Jaden's helpers. Unlimited capacity displays
  as No limit; remaining spaces never go below zero.
- Successful-payment totals sum saved `Payment.amount_cents` where status is
  `succeeded`, through registrations for the supplied owned events. Pending and
  declined, refund-pending and refunded payments are excluded; registrations alone never imply payment.
  `revenue_available=True`; no payments returns `revenue_cents=0` and
  `revenue_label='$0.00'`. The total is gross successful ticket payments before
  Stripe fees, not a bank balance. Stripe event cancellation/moderation queues
  refunds and retains payment records with their updated status. The isolated
  mock provider remains available for automated tests and development previews.
- Organizer routes set `event_management_available=True` and
  `event_browsing_available=True`; create, edit and public-event links are connected.

To try these real pages without configuring Google or changing real data, run
`python tools/preview_organizer.py --port 5075`, then visit
`http://localhost:5075/demo`. The dedicated runner uses in-memory SQLite, a
separate session cookie and fictional records. It is loopback-only; its `/demo`
login shortcut and banner are never registered by the production factory.
Restarting resets it. The main demo has 3 published events and 27 confirmed
registrations. Build & Brew has capacity 8 and 8 confirmed signups. The preview
does not seed payment or ticket records.
Sunset Cinema has 18 confirmed signups, 22 remaining spaces and year totals
6/4/3/2/1/2. Other examples cover unlimited capacity and cancelled events.
No seed/reset is performed against the real database.

Hosting access uses `User.can_organize`: a Google-linked account in the configured
SCU email domain with the student, organizer or admin role. Ordinary Google
signups remain students and can create/manage their own events immediately.
Existing organizer accounts remain compatible. No role promotion or individual
attendee removal is needed. Admin moderation still requires the separate admin
role, and paid-event creation/editing verifies the owner’s Stripe readiness.
The demo account switches do not exercise Google OAuth.

Run `python -m pytest -q tests tools` for the application and existing preview
checks. Refresh pages after backend records change; there is no live polling.

## Event reporting and moderation

- `GET/POST /events/<id>/report` accepts reports from signed-in student,
  organizer or admin accounts about another person's published event. A reason
  and 10–2,000 characters of detail are required. One report per account/event is
  retained, including after resolution; duplicates never overwrite the original.
- `GET /admin/reports?status=pending|resolved` is admin-only. Pending reports are
  oldest first; resolved reports are most recently reviewed first. Each card
  opens `GET /admin/reports/<id>` with the event and full concern side by side.
- `POST /admin/reports/<id>/dismiss` closes that report without changing the
  event. `POST /admin/reports/<id>/remove` marks the event `removed`, cancels
  confirmed/pending registrations and resolves all pending reports for it as
  removed. Existing resolved reports remain unchanged. These writes commit
  together; the first decision on a pending report wins over stale submissions.
- Both decisions require a 10–1,000 character reason. `Report` stores reviewer,
  review time and note. Students see their own receipt/status, not private admin
  review notes. Admin report dates and reporter receipt dates use Pacific Time
  with a PST/PDT label; stored timestamps remain UTC.
- All moderation/report POSTs validate a session- and user-bound CSRF token.
  Roles come from the signed-in database user. No account promotion is added.
  Removal retains event, registration and report rows. It is distinct from an
  organizer cancellation (`cancelled`) and cannot be undone by editing the event.
- Removed public details return 404. Attendee receipts hide the removed title,
  description, location, organizer and cover; the owning organizer and admins
  retain private review access. Removed covers are unavailable to everyone else.
- Startup adds missing tables and applies the additive SQLite upgrade described
  under Event cover upload below. Never run a preview seed against the real database.

The same port 5075 demo includes `/demo/admin` and `/demo/student`; those login
shortcuts are registered only by the separate in-memory demo factory, never the
production app. It starts with three pending reports and one dismissed report.
Two pending reports target Build & Brew, demonstrating that one removal resolves
both. Removing it changes the organizer's confirmed total from 27 to 19.

The public event route sets `event_reporting_available=True` for an eligible
signed-in user. Reporting and admin routes enable `event_browsing_available`,
so reporters can return to a published/cancelled event. Admins review removed
content within their authorized report view. Public discovery excludes cancelled
and removed events, and registration rejects them. Cancelled/removed events and
cancelled registrations cannot display a valid paid ticket. For real Stripe
purchases, moderation queues full refunds and marks affected payment records
refund-pending until provider verification. Individual attendee removal
is outside this slice.

Validation covers role/CSRF boundaries, invalid input, duplicate reports,
private review notes, stale decisions, atomic cancellation and unrelated records.
The existing port 5074 sample preview remains compatible with its older reports
context; real templates use `moderation_enabled=True` and `moderation.css`.

## Run the preview

From `event-hub`, install the separate preview dependencies (or use `Start Preview.command`):

```sh
python -m pip install -r tools/requirements-preview.txt
python tools/preview_ui.py --port 5074
```

Open `http://127.0.0.1:5074`. The runner binds only to the local computer and
does not enable Flask debug mode. Its sample date is **October 8, 2026**, so
Today, This week, and This month remain reproducible during reviews.

Use the preview role menu for Student, Organizer, Admin, and Guest views.
Reset sample data restores the initial fixtures. Sample changes live in a
signed browser-session cookie, with a random key per server run. They are not
a database, are not encrypted, and are cleared when the preview is reset or
the server is restarted. Enter only fictional display names/event information.
The preview does not read or store passwords or card numbers.
Uploaded covers are held in process memory, scoped to that preview session;
only opaque references go in the cookie. Resetting or restarting clears them.

## Ownership and integration boundary

- Ethan: UI templates, styles, client-side interaction, previews, organizer
  dashboard/statistics, event reporting and admin moderation.
- Jaden: shared application setup and event/registration work.
- Patrick: account registration, Google login and the original mock payment/ticket behavior.
  Ethan is now integrating organizer-owned Stripe payments, hosted onboarding,
  checkout verification and refund recovery. The older UI preview demonstrates
  only the visual handoff and a sample entry action, with no real sign-in.

The real application renders the same templates using the context below. Keep
`preview_mode=False` in the real app and do not expose the `/preview/*` routes.
The role menu is deliberately only a local demo selector, never an account
permission system. Real role/ownership checks, CSRF protection, validation,
session configuration, payment verification, and duplicate-request handling
belong in the actual application. The local runner is not production-ready.

## Shared template context

| Value | Shape |
| --- | --- |
| `preview_mode` | Boolean; shows the fictional-data notice and role menu |
| `user` | `None` for guest, otherwise `{id,name,first_name,email,role,year_level,can_organize}` |
| `active_nav` | `home`, `explore`, `my-events`, `organizer`, or `admin` |
| `categories` | List of category labels |
| `errors` | Form-field-to-message dictionary, empty on first view |
| `form_values` | Submitted values preserved on validation failure |
| `registration_by_event` | Active registration views keyed by integer event ID |
| Flash messages | Flask `get_flashed_messages(with_categories=True)` |

Templates may include a CSRF field when a callable `csrf_token` is supplied.

Event views contain `id`, `title`, `description`, `category`, `location`,
`organizer`, `date_label`, `day`, `month`, `time_label`, `price_cents`,
`price_label`, `art`, `status`, `capacity`, `registered`,
`registrations_count`, `start_value`, and `end_value`. Dates for form controls
use `YYYY-MM-DDTHH:MM`. `status` is `published`, `cancelled` or `removed`. Artwork is a local
filename, never user HTML. `capacity=None` means unlimited and omits `spots_left`;
finite capacities include the remaining count in `spots_left`.
An optional trusted `cover_url` overrides that default
artwork across event cards, detail, organizer and registration screens. Owner identity is checked by the route, not trusted
from a submitted field.

Registration views contain `id`, nested `event`, `status`, `payment_status`,
`ticket_code`, and `ticket_valid`. States are:

- `status`: `confirmed`, `pending`, `failed`, or `cancelled`.
- `payment_status`: `not_required`, `succeeded`, `pending`, `failed`,
  `refund_pending`, or `refunded`.
- A valid paid ticket requires a saved valid Ticket, confirmed registration,
  a published event that has not ended and a successful payment. Free registrations use their
  confirmation rather than a paid ticket code. The older preview codes begin `DEMO-BH-` and
  are not admission credentials. Pending/failed payments never issue a code.

Real stats contain `registrations`, `revenue_available`, `revenue_cents` and
`revenue_label`. Dashboard stats additionally contain `events_count`; event
stats additionally contain `year_breakdown` entries `{label,count,percent}`.
The older UI preview supplies invented aggregate counts/year splits, with no
`revenue_available` flag (templates default it to true for that preview only).
The actual service sums successful saved payments for owned events and keeps
registration counts distinct from checked-in attendance.

## Routes and page-specific context

| Route | Template and context |
| --- | --- |
| `GET /` | `index.html`: events, featured_event |
| `GET /events` | `events/list.html`: events, filters, categories, campus-week context below |
| `GET /events/<id>` | `events/detail.html`: event, registration, related_events |
| `GET/POST /events/<id>/register` | `registrations/confirm.html`: event |
| `GET/POST /registrations/<id>/checkout` | `registrations/checkout.html`: registration, event |
| `GET /my-registrations` | `registrations/my_registrations.html`: registrations |
| `GET /my-registrations/<id>` | `registrations/detail.html`: registration, event |
| `POST /my-registrations/<id>/cancel` | Free-registration cancellation, then redirect |
| `GET /organizer/` | `organizer/dashboard.html`: owned events, stats |
| `GET /organizer/payments` | `organizer/payments.html`: own connected account and payment setup availability |
| `POST /organizer/payments/onboard` | CSRF-protected Stripe hosted onboarding for the current organizer |
| `POST /organizer/payments/refresh` | API verification of the current organizer's account capabilities |
| `GET /organizer/payments/return` | Re-fetch account status; query parameters cannot approve an account |
| `GET /organizer/payments/expired` | Prompt for a new CSRF-protected setup request |
| `POST /billing/webhook` | Signature-verified connected-account payment/refund notifications |
| `GET/POST /organizer/events/new` | `organizer/event_form.html`: event=None, mode=create |
| `GET/POST /organizer/events/<id>/edit` | `organizer/event_form.html`: event, mode=edit |
| `POST /organizer/events/<id>/cancel` | Owner-only cancellation, then redirect |
| `GET /organizer/events/<id>/stats` | `organizer/stats.html`: event, stats |
| `GET /media/events/<id>/cover` | Stored cover; removed media is limited to its owner/admins |
| `GET /login`, `/signup` | Corresponding `auth/` template; Google sign-in entry |
| `GET/POST /profile` | `auth/profile.html`: current account and edit form |
| `GET/POST /logout` | Sign out of the current session |
| `GET/POST /events/<id>/report` | `events/report.html`: event, reasons, existing_report, errors, form_values |
| `GET /admin/reports` | `admin/reports.html`: reports, filter_status, counts={pending,resolved} |
| `GET /admin/reports/<id>` | `admin/report_detail.html`: report, event, errors, form_values, attempted_action |
| `POST /admin/reports/<id>/dismiss` | Admin-only dismissal with review_note and csrf_token |
| `POST /admin/reports/<id>/remove` | Admin-only removal with review_note and csrf_token |
| Error response | `errors/error.html`: status, title, message |

Search query names: `q`, `category`, `date`, `location`, `calendar`. Real date values
are blank, `today`, `week`, `month`, or an exact `YYYY-MM-DD`; `calendar` selects
the visible week independently. Organizer form names: `title`, `description`,
`category`, `location`, `start_time`, `end_time`, `price`, `capacity`, optional
`cover_image`, and `remove_cover`.
The older UI preview checkout uses `demo_result=succeeded|declined|pending`.
The real app defaults to `PAYMENT_PROVIDER=disabled`. With Stripe configured,
organizers explicitly choose a US-based account and complete Stripe onboarding.
Each gets a full Stripe Dashboard, Stripe-managed processing fees/losses, and
direct charges with no BroncosHub application fee. Card/bank details stay at
Stripe. Account setup return URLs never prove verification; the API does.

The real checkout POST reserves a seat for 31 minutes and redirects to hosted
Checkout. Its stable server-side attempt stores the price/account and reuses an
idempotency key on retries. Signed events or API reconciliation confirm payment
before issuing a ticket; merely returning to the receipt cannot confirm it.
Cancellation/moderation closes checkout and queues full refunds. Refunds remain
pending until verified; delayed/failed refunds remain visible for recovery.
There is no attendee self-service paid cancellation or organizer attendee-kick
feature. External partial refunds invalidate admission and enter full-refund
reconciliation. Deployment variables, webhook subscriptions and the recovery
worker are documented in [deployment.md](deployment.md).

The isolated development/test `mock` provider retains Patrick's original behavior:
test numbers beginning `4242` succeed, `4000` decline and `4999` stay pending.
It saves test Payment records and never contacts Stripe. Production rejects mock
configuration. Preview actions and mocked payment tests are not live charges.

Profile fields are `name` and optional `year_level`, whose values are
`first_year`, `second_year`, `third_year`, `fourth_year`, `graduate`, `unknown`.
Preview signup validates and saves only these two fictional profile fields;
the fixed sample email and disabled passwords do not create an account.
Real report views contain `{id,event,reason,details,reporter,date_label,status,
review_note,reviewed_by,reviewed_date_label}`. The reporter-facing receipt gets
only `{id,status,date_label}` for their own report. Admin notes are not public.
The older all-pages sample preview uses `{id,event,reason,reporter,date_label}`.

### Profile overview and editing

`auth/profile.html` receives `profile_overview` with `upcoming_count`,
`pending_count`, and `next_registration` (a registration view or null). The real
route queries only the current user's confirmed/pending registrations for
published events that have not ended in Pacific Time, including ongoing events.
Confirmed registrations count as upcoming plans; pending paid registrations
count separately. The earliest confirmed registration by event start is the next
plan. Cancelled, removed, ended and failed registrations do not appear here.

The identity banner uses saved `user` values, while rejected edits stay in
`form_values`. Keep those contexts separate. Email is displayed as static sign-in
information; only `name` and `year_level` are submitted. `profile.js` enables Save
for changes and restores persisted values on Discard, including after validation
errors. Profile POSTs validate the session/user-bound CSRF token and still work
without JavaScript. Unknown year levels have no
banner badge. The next-event card links to the registration's ticket; a missing
next registration produces an Explore link. Hosting and admin shortcuts do not grant
access; the actual routes must enforce authorization.

The `page_assets` block in `base.html` loads `profile.css` and `profile.js` only on
the profile page. Name/year edits persist on the existing User record; changing
profile fields does not change the account's sign-in identity or role.

The older preview rejects event start dates before its fixed sample date and
locks admission price after a confirmed or pending registration. The real event
validator uses Pacific campus time, rejects new past start times and rejects
price changes once the event has any registration records. The preview's readonly
form behavior is gated by `preview_mode`; real validation runs server-side.
An event view may include
`price_locked` to indicate a pending registration even when its confirmed
registration count is still zero.

## Verification

```sh
python -m pytest -q tests tools
python -m unittest discover -s tools -p 'test_*.py' -v
```

The pytest command covers the application and preview tests; the unittest
command runs the older UI preview checks alone. Coverage includes template
rendering, filtering, role/owner boundaries,
free registration/cancellation, paid confirmation and invalid tickets,
duplicate requests, organizer validation, escaping, session separation,
safe redirects, moderation, and avoiding credential storage. They do not
exercise live Google OAuth or a real payment provider. Also review keyboard
navigation, mobile layouts, focus return, and reduced motion in a browser.


## Campus week and live draft UI

Real Explore receives `campus_days` (seven entries with
`{iso,weekday,day,month,label,count,url,selected}`), `clear_date_url`, `date_label`,
`exact_date_label`, and `result_announcement`. It starts from today's Pacific date.
Counts include published, unended events matching the text/category/location
filters, before applying the selected date. Overnight events count on each day
they overlap; ending at midnight does not count on the next day. One aggregate
query calculates the visible week's counts without loading intervening weeks.
The older UI preview still uses its fixed October 8, 2026 sample date.

The route also supplies `calendar_value`, `calendar_previous`/`calendar_next`
(native URLs, or null at a boundary), and `calendar_range`. Browsing weeks
preserves search/category/location/date, while “All upcoming” clears only the
date selection. A deep link with an exact `date` opens its containing week;
an explicit `calendar` can show another week without changing that selection.
Week starts are normalized relative to today and cannot go into the past. There
is no sample-event cutoff for future browsing; the limit is the final complete
week supported by Python's date range. Invalid dates fall back safely. Mobile
users can horizontally scroll the days and use the same week arrows.

The native links work without application JavaScript. `campus-week.js` progressively fetches the same HTML route and updates `#campus-results`, date choices and form values. Keep the current DOM IDs/data attributes for that enhancement. It aborts superseded requests, keeps URLs/history consistent, distinguishes hash-only navigation, and falls back to normal page loading on failure. View Transitions are optional and skipped for reduced-motion preferences. Mobile filters use a native disclosure; they remain open if the script is unavailable.

Despite its filename, `organizer-preview.js` also enhances the real event editor.
It reads form fields and writes `textContent`, formats campus wall time, shows
unsaved changes and previews category artwork or the chosen cover. The normal
multipart form submission saves the event and image; JavaScript does not persist
either. Existing covers remain selected when the category changes.

## Event cover upload

The create/edit form submits `multipart/form-data`. `cover_image` is an optional
JPEG, PNG or WebP file, maximum 5 MiB. The UI has a native file picker, drag/drop,
live crop preview, replacement and removal. Category changes retain a chosen
cover. Images are centered with `object-fit: cover`; no manual crop tool is implied.
Both the client and server check the upload; client checks are not trusted.

The real app stores generated filenames in nullable `Event.cover_image_path`.
`event.cover_url` points to `/media/events/<id>/cover`. Uploads are decoded as
still images, limited to 20 MP and 10,000 pixels per side, oriented, resized to
at most 2400px and re-encoded as WebP without original metadata. The original
filename is not used for storage. Create/edit enforce organizer ownership and
CSRF, and reject duplicate uploads or simultaneous replacement/removal.

`remove_cover=1` restores category artwork. Validation errors leave the saved
event/cover unchanged; a newly chosen image must be selected again after fixing
other form errors. A failed database save cleans up the new file, while the old
file is deleted only after a successful replacement/removal. Native multipart
selection and the existing-cover removal checkbox work without JavaScript.

The default directory is `instance/uploads/event-covers`. `COVER_UPLOAD_DIR`
may be absolute or relative to Flask's instance directory. Keep the database and
this directory on persistent storage and back up both. Media responses are
uncached and check current event access: removed covers are available only to
the owning organizer or an admin, and other users receive 404.

Startup adds the nullable cover column to an existing SQLite database without
resetting records. The upgrade also converts legacy `cancelled` events with a
`removed` report to the distinct `removed` status. Other database types require
the cover-column migration before startup. This does not replace backups.

The older UI-only preview uses a separate `cover_token`, scoped to the session,
to retain a validated draft through form errors. That token is not a storage
mechanism used by the real app. The preview uses Pillow to verify still rasters up to 20 MP and 10,000
pixels per side, correct EXIF orientation, resize to at most 2000px and re-encode
fresh JPEG pixels without metadata (transparency becomes white). Files never
enter Git or the session cookie. In-memory storage has explicit per-session and
global count/byte bounds, and `/preview/covers/<token>` is private to that session.
This is demo storage and clears on reset/restart; it is separate from the real
app's database-linked files and event-bound media access.

New presentation assets `campus-week.css`, `organizer-preview.css`, `ticket.css`, `campus-week.js` and `organizer-preview.js` are included by `base.html`. All events, registrations, accounts and payments in the local runner remain fictional demonstrations.
