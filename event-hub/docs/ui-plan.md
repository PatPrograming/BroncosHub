# BroncosHub UI plan

Reviewed October 6, 2026. This plan comes before the UI implementation and follows the current repository's Flask/Jinja scaffold.

## What we are building

A campus-only event hub for Santa Clara University students. The first screen should feel like opening a campus magazine: a strong headline, one featured event, and useful event listings immediately below. Every page should feel like part of the same product.

Ethan's scope is every page's presentation, navigation, responsive layout and interaction states. Patrick owns account registration/login behavior. Jaden owns the application framework, data and business rules. The existing Python application files stay available for their work. A separate local preview uses sample data so the UI can be reviewed before those pieces are ready.

## Page map

| Area | Pages | Main action |
| --- | --- | --- |
| Home | `/` | Find an event or explore a category |
| Explore | `/events` | Search and filter by category, date and location |
| Event | `/events/<id>` | Read details and register |
| Registration | `/events/<id>/register` | Review a free registration or continue to checkout |
| Checkout | `/registrations/<id>/checkout` | Review price and see demo payment outcomes |
| My Events | `/my-registrations` and `/my-registrations/<id>` | View registrations, payment status and tickets |
| Organizer | `/organizer/` | Manage events and see registration totals |
| Event editor | `/organizer/events/new` and `/organizer/events/<id>/edit` | Create or update an event |
| Statistics | `/organizer/events/<id>/stats` | See registrations, optional year-level breakdown and confirmed revenue |
| Account | `/login`, `/signup`, `/profile` | Account screens for Patrick to connect |
| Admin | `/admin/reports` | Review reports, as included in the repository scaffold |
| System | Error/permission pages | Recover through a useful link |

Desktop navigation: Home, Explore, My Events, plus Organize for organizer/admin accounts. Small screens use an accessible menu. Account actions live on the right. An obvious, separate preview control lets the team inspect roles; it is not part of production authentication.

## Visual direction

- Warm cream background, charcoal text, BroncosHub deep red, with orange, green and violet in original event artwork. This is a proposed product palette, not a claim to reproduce SCU's official brand standards.
- Large editorial serif headlines paired with clean system sans-serif labels and body copy.
- A consistent content width, generous spacing, quiet borders, rounded cards, clear prices and compact date labels.
- Three event cards across on desktop, two at intermediate widths and one on small phones. Details use a reading column and a registration panel that stacks on mobile.
- Motion supports actions: short hover transitions, gentle entrance animation, useful dialog transitions. Reduced-motion preferences remove nonessential animation.
- Use real links for navigation, visible keyboard focus, labeled fields, explicit empty/error/success states and confirmation before cancellation.

## Research and choices

Primary product and accessibility sources reviewed October 6, 2026. These informed the structure; their artwork, layouts and branding are not copied.

- [Luma Discover](https://luma.com/discover): lead with local discovery and simple categories. BroncosHub uses a fixed SCU campus context rather than asking students to pick a city.
- [Luma registration flow](https://help.luma.com/p/event-registration-process): keep event information, registration and confirmation distinct, and make an existing registration visible.
- [Partiful Explore](https://partiful.com/explore): let expressive event artwork carry the personality while preserving consistent dates, venue and price metadata.
- [Eventbrite event ticketing](https://www.eventbrite.com/event-ticketing/free/): distinguish discovery from the organizer's event and registration tools.
- [CampusGroups campus events](https://www.readyeducation.com/campusgroups/streamline-university-event-management-campusgroups/): use campus locations and organizer identities as useful context. Institutional approval and room-booking systems are outside this UI's scope.
- [Localist student event calendars](https://concept3d.com/student-event-calendar-software/): put search and filters near event discovery and group the event form into understandable sections.
- [USWDS cards](https://designsystem.digital.gov/components/card/), [GOV.UK validation](https://design-system.service.gov.uk/patterns/validation/), [WAI dialogs](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) and [WCAG 2.2](https://www.w3.org/TR/WCAG22/): use semantic cards, visible labels and errors, keyboard-accessible dialogs, readable contrast and responsive reflow.
- [MDN reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion): respect the student's motion preference.

## Boundaries and review

Sample events are fictional and clearly labeled in the preview. The preview does not create real accounts, send payments, validate admission tickets or establish production authorization. Registration counts represent expected attendance. Revenue includes succeeded payments only. Paid cancellation/refunds remain a team decision. Capacity and admin reports are included because the current repository explicitly models them.

Review all routes, search/empty results, registration/payment states, cancellation, invalid forms, mobile layouts, keyboard navigation and reduced motion. Keep the work on its own feature branch and check remote changes before handoff. Do not merge or publish without a separate request.
