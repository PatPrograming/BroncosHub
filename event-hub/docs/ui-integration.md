# BroncosHub UI integration

The templates, shared styles, JavaScript, and local artwork form the UI layer.
`tools/preview_ui.py` is a separate local preview for reviewing that layer while
the remaining routes and services are being built. It does not replace the real
application. Google authentication, organizer pages and event moderation now use
the actual Flask application and database models; payments and other event flows remain
unfinished.

## Organizer dashboard and statistics — October 14, 2026

Built on Jaden's `4335dfa` Event and Registration models, without changing their
schema or Patrick's account routes:

- `GET /organizer/` lists only the signed-in organizer's events, with current and
  upcoming events first, then historical/cancelled events. Admins also see only
  their own events in this section. Guests go to login; students receive 403;
  unavailable or other owners' event statistics return 404.
- `GET /organizer/events/<id>/stats` shows confirmed registration counts and year
  totals. Pending, failed and cancelled registrations are excluded. Missing or
  unrecognized year levels are Unknown. The denominator is all confirmed signups
  for that event; rounded percentages may not sum to exactly 100%.
- Dashboard totals cover all owned events. Published event count includes past
  and upcoming published events; confirmed signup count includes historical
  records on cancelled events if any remain. These are signups, not check-ins.
- Capacity and remaining spaces use Jaden's helpers. Unlimited capacity displays
  as No limit; remaining spaces never go below zero.
- Payment records are not implemented. `revenue_available=False`,
  `revenue_cents=None` and `revenue_label='—'` explicitly mean unavailable, not $0.
  Connect only successful Payment records when that service is ready; never
  derive revenue from event prices or registration counts.
- The organizer routes set `event_management_available=False` and
  `event_browsing_available=False` to hide unfinished create/edit/public-detail
  links. Enable these in `app/routes/organizer.py` when those shared routes land.
  The old UI preview defaults both to true. Global navigation remains the shared
  UI; its other production destinations still need their respective routes.

To try these real pages without configuring Google or changing real data, run
`python tools/preview_organizer.py --port 5075`, then visit
`http://localhost:5075/demo`. The dedicated runner uses in-memory SQLite, a
separate session cookie and fictional records. It is loopback-only; its `/demo`
login shortcut and banner are never registered by the production factory.
Restarting resets it. The main demo has 3 published events and 27 confirmed
registrations. Sunset Cinema has 18 confirmed signups, 22 remaining spaces,
and year totals 6/4/3/2/1/2. Other examples cover full, unlimited, empty and
cancelled events. No seed/reset is performed against the real database.

Production access requires an existing organizer/admin role stored on the user;
ordinary Google signups continue to create student accounts. This feature does
not add role promotion, individual attendee removal or payment handling. Admin
event moderation is described below.

Run `python -m pytest -q tests tools` for the application and existing preview
checks. Refresh pages after backend records change; there is no live polling.

## Event reporting and moderation — October 14, 2026

- `GET/POST /events/<id>/report` accepts reports from signed-in student,
  organizer or admin accounts about another person's published event. A reason
  and 10–2,000 characters of detail are required. One report per account/event is
  retained, including after resolution; duplicates never overwrite the original.
- `GET /admin/reports?status=pending|resolved` is admin-only. Pending reports are
  oldest first; resolved reports are most recently reviewed first. Each card
  opens `GET /admin/reports/<id>` with the event and full concern side by side.
- `POST /admin/reports/<id>/dismiss` closes that report without changing the
  event. `POST /admin/reports/<id>/remove` marks the event cancelled, cancels
  confirmed/pending registrations and resolves all pending reports for it as
  removed. Existing resolved reports remain unchanged. These writes commit
  together; the first decision on a pending report wins over stale submissions.
- Both decisions require a 10–1,000 character reason. `Report` stores reviewer,
  review time and note. Students see their own receipt/status, not private admin
  review notes. Dates in this moderation history are explicitly labeled UTC.
- All moderation/report POSTs validate a session- and user-bound CSRF token.
  Roles come from the signed-in database user. No account promotion is added.
  Removal is a soft cancellation; no event, registration or report is deleted.
- The app imports the new `Report` model before its existing `db.create_all()`.
  This adds a table without changing Jaden's Event/Registration or Patrick's
  User schema. Follow the team's migration process when deploying to an existing
  shared database; never run the demo seed against it.

The same port 5075 demo includes `/demo/admin` and `/demo/student`; those login
shortcuts are registered only by the separate in-memory demo factory, never the
production app. It starts with three pending reports and one dismissed report.
Two pending reports target Build & Brew, demonstrating that one removal resolves
both. Removing it changes the organizer's confirmed total from 27 to 19.

**Remaining integration:** `app/routes/events.py` and registration routes are
still stubs. The public event-detail template exposes its report link only when
the renderer sets `event_reporting_available=True`; connect that flag when the
real detail route is ready. Until then the production report form is directly
addressable, and the demo's Report an event link exercises it. Public discovery
must exclude cancelled events, and registration must reject them. Payment/Ticket
models are unfinished: moderation does not issue refunds or change payment
records. Connect cancellation/refund and ticket validity rules when those
services exist. Individual attendee removal is outside this slice.

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
- Patrick: account registration and login behavior. The older UI preview demonstrates
  only the visual handoff and a sample entry action, with no real sign-in.

Jaden can render the same templates by supplying the context below. Replace
preview routes and fixtures with the application’s models/services. Keep
`preview_mode=False` in the real app and do not expose the `/preview/*` routes.
The role menu is deliberately only a local demo selector, never an account
permission system. Real role/ownership checks, CSRF protection, validation,
session configuration, payment verification, and duplicate-request handling
belong in the actual application. The local runner is not production-ready.

## Shared template context

| Value | Shape |
| --- | --- |
| `preview_mode` | Boolean; shows the fictional-data notice and role menu |
| `user` | `None` for guest, otherwise `{id,name,first_name,email,role,year_level}` |
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
use `YYYY-MM-DDTHH:MM`. `status` is `published` or `cancelled`. Artwork is a local
filename, never user HTML. `capacity=None` means unlimited and omits `spots_left`;
finite capacities include the remaining count in `spots_left`.
An optional trusted `cover_url` overrides that default
artwork across event cards, detail, organizer and registration screens. Owner identity is checked by the route, not trusted
from a submitted field.

Registration views contain `id`, nested `event`, `status`, `payment_status`,
`ticket_code`, and `ticket_valid`. States are:

- `status`: `confirmed`, `pending`, or `cancelled`.
- `payment_status`: `not_required`, `succeeded`, `pending`, or `failed`.
- A valid ticket requires confirmed registration, a published event, and a
  successful payment for paid events. The preview codes begin `DEMO-BH-` and
  are not admission credentials. Pending/failed payments never issue a code.

Real stats contain `registrations`, `revenue_available`, `revenue_cents` and
`revenue_label`. Dashboard stats additionally contain `events_count`; event
stats additionally contain `year_breakdown` entries `{label,count,percent}`.
The older UI preview supplies invented aggregate counts/year splits, with no
`revenue_available` flag (templates default it to true for that preview only).
The actual service leaves revenue unavailable until successful payments can be
verified and keeps registration counts distinct from checked-in attendance.

## Routes and page-specific context

| Route | Template and context |
| --- | --- |
| `GET /` | `index.html`: events, featured_event |
| `GET /events` | `events/list.html`: events, filters, locations, optional campus-week context below |
| `GET /events/<id>` | `events/detail.html`: event, registration, related_events |
| `GET/POST /events/<id>/register` | `registrations/confirm.html`: event |
| `GET/POST /registrations/<id>/checkout` | `registrations/checkout.html`: registration, event |
| `GET /my-registrations` | `registrations/my_registrations.html`: registrations |
| `GET /my-registrations/<id>` | `registrations/detail.html`: registration, event |
| `POST /my-registrations/<id>/cancel` | Free-registration cancellation, then redirect |
| `GET /organizer/` | `organizer/dashboard.html`: owned events, stats |
| `GET/POST /organizer/events/new` | `organizer/event_form.html`: event=None, mode=create |
| `GET/POST /organizer/events/<id>/edit` | `organizer/event_form.html`: event, mode=edit |
| `POST /organizer/events/<id>/cancel` | Owner-only cancellation, then redirect |
| `GET /organizer/events/<id>/stats` | `organizer/stats.html`: event, stats |
| `GET/POST /login`, `/signup`, `/profile` | Corresponding `auth/` template |
| `GET/POST /logout` | Preview only: switch to guest |
| `GET/POST /events/<id>/report` | `events/report.html`: event, reasons, existing_report, errors, form_values |
| `GET /admin/reports` | `admin/reports.html`: reports, filter_status, counts={pending,resolved} |
| `GET /admin/reports/<id>` | `admin/report_detail.html`: report, event, errors, form_values, attempted_action |
| `POST /admin/reports/<id>/dismiss` | Admin-only dismissal with review_note and csrf_token |
| `POST /admin/reports/<id>/remove` | Admin-only cancellation with review_note and csrf_token |
| Error response | `errors/error.html`: status, title, message |

Search query names: `q`, `category`, `date`, `location`. Date values are blank,
`today`, `week`, `month`, or an exact `YYYY-MM-DD` date. Organizer form names: `title`, `description`,
`category`, `location`, `start_time`, `end_time`, `price`, and `capacity`.
The preview checkout uses `demo_result=succeeded|declined|pending`. Replace
that sample result selector with the team’s verified payment integration later.
Paid cancellation/refunds are intentionally unavailable pending the team’s
policy and implementation decision.

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
`pending_count`, and `next_registration` (a registration view or null). Count
only the current user's upcoming, published events with valid confirmed tickets;
pending registrations appear separately. Choose the earliest valid registration
by event start. The preview includes today using its fixed sample date; the real
service should use campus time and the team's agreed event-expiry rule.

The identity banner uses saved `user` values, while rejected edits stay in
`form_values`. Keep those contexts separate. Email is displayed as static sign-in
information; only `name` and `year_level` are submitted. `profile.js` enables Save
for changes and restores persisted values on Discard, including after validation
errors. Native POST still works without JavaScript. Unknown year levels have no
banner badge. The next-event card links to the registration's ticket; a missing
next registration produces an Explore link. Role-specific shortcuts do not grant
access; the actual routes must enforce authorization.

The `page_assets` block in `base.html` loads `profile.css` and `profile.js` only on
the profile page. Authentication and durable account changes remain with Patrick
and Jaden's implementation.

The preview rejects event start dates before its fixed sample date. It also
locks admission price after a confirmed or pending registration, keeping
existing free registrations and payment attempts consistent. This is a
provisional preview rule, not an agreed production policy. The readonly form
behavior is gated by `preview_mode`; the team must decide and implement the
real price-change policy during backend integration. An event view may include
`price_locked` to indicate a pending registration even when its confirmed
registration count is still zero.

## Verification

```sh
python -m unittest discover -s tools -p 'test_*.py' -v
```

The checks exercise real template rendering, filtering, role/owner boundaries,
free registration/cancellation, paid confirmation and invalid tickets,
duplicate requests, organizer validation, escaping, session separation,
safe redirects, moderation, and avoiding credential storage. They do not
establish that the team’s unfinished backend works. Also review keyboard
navigation, mobile layouts, focus return, and reduced motion in a browser.


## Campus week and live draft UI

Explore optionally receives `campus_days` (seven entries with `{iso,weekday,day,month,label,count,url,selected}`), `clear_date_url`, `date_label`, `exact_date_label`, and `result_announcement`. Counts apply the text/category/location filters before applying the selected date. Each date URL preserves those filters. In production the starting date should come from the campus timezone; the isolated preview deliberately uses October 8, 2026.

The calendar also needs `calendar_value` (the first visible date as ISO, including the initial week), `calendar_previous`/`calendar_next` (native URLs, or null at a boundary), and `calendar_range` (readable dates/year). The `calendar=YYYY-MM-DD` query controls the visible week independently of `date`, which filters results. Preserve search/category/location/date while browsing weeks and preserve calendar state on form submission. A deep link with only an exact `date` opens its containing week; an explicit `calendar` may show another week while that date stays selected in the When field. The preview normalizes week starts relative to today, prevents browsing into the past, and allows later weeks beyond the sample event dates. Mobile users can horizontally scroll the days and use the same week arrows.

The native links work without application JavaScript. `campus-week.js` progressively fetches the same HTML route and updates `#campus-results`, date choices and form values. Keep the current DOM IDs/data attributes for that enhancement. It aborts superseded requests, keeps URLs/history consistent, distinguishes hash-only navigation, and falls back to normal page loading on failure. View Transitions are optional and skipped for reduced-motion preferences. Mobile filters use a native disclosure; they remain open if the script is unavailable.

`organizer-preview.js` enhances `data-event-editor`/`data-event-form` only. It reads existing fields and writes `textContent`, never saving a form or injecting user HTML. It formats datetime-local values as campus wall time, shows a dirty state and changes preview artwork by category. Provide `draft_art` when rendering validation responses so the poster agrees with the field values. Existing artwork is preserved if the category is unchanged; category-to-art mapping in the preview runner matches the JS mapping. This is provisional local artwork behavior; integrate the actual upload/artwork policy with the framework later.

## Event cover upload

The create/edit form submits `multipart/form-data`. `cover_image` is an optional
JPEG, PNG or WebP file, maximum 5 MiB. The UI has a native file picker, drag/drop,
live crop preview, replacement and removal. Category changes retain a chosen
cover. Images are centered with `object-fit: cover`; no manual crop tool is implied.
The client checks size/type/decoding, but the server must independently validate.

Supply `cover_url`, `cover_name` and `cover_token` to the editor, using empty
strings when absent. A server-issued, session/owner-bound `cover_token` retains
a validated draft through other form errors; never trust arbitrary client URLs.
`remove_cover=1` requests the default artwork. Preserve that flag on validation
failure. Report cover errors in `errors.cover_image`. A rejected replacement
keeps the prior valid cover; the UI lets the organizer keep it and continue.
Native multipart selection works without app JavaScript, including removal of
an existing cover through a fallback checkbox.

The isolated preview uses Pillow to verify still rasters up to 20 MP and 10,000
pixels per side, correct EXIF orientation, resize to at most 2000px and re-encode
fresh JPEG pixels without metadata (transparency becomes white). Files never
enter Git or the session cookie. In-memory storage has explicit per-session and
global count/byte bounds, and `/preview/covers/<token>` is private to that session.
This is demo storage. Jaden's production integration still needs durable image
storage, real ownership/access checks and cleanup through the actual app.

New presentation assets `campus-week.css`, `organizer-preview.css`, `ticket.css`, `campus-week.js` and `organizer-preview.js` are included by `base.html`. All events, registrations, accounts and payments in the local runner remain fictional demonstrations.
