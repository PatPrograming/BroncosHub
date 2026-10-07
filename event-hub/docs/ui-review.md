# UI review — October 6, 2026

Screenshot paths below refer to local review captures. Those folders are excluded
from Git; the templates, artwork, preview and tests are included in the repository.

## Readability and motion revision

Three agents independently reviewed the student routes, organizer/account/admin routes, and motion/accessibility choices. The visual agents inspected rendered pixels and saved baseline and revised screenshots in `docs/review-v2/`. This revision keeps the accepted warm cream, crimson, original event artwork and editorial headings.

### Findings and changes

- Replaced 9–13px navigation, event metadata and form copy with shared readable sizes: supporting information at least 14px, controls 16px, body 16–17px. Stronger input outlines and placeholder contrast; responsive rules no longer shrink important copy.
- Fixed the search icon overlapping input text. Navigation collapses before the larger labels become crowded. Phone headlines, forms, registration review, dashboard cards and report actions were adjusted to use available space.
- Removed whole-page fading. Text appears fully readable immediately; animation now accompanies changes in the interface.
- Added the seven-day campus rail on Explore. Real date links filter the server-rendered events; progressive enhancement moves the crimson selection and transitions the actual result cards. Search/category/location, typed filters, browser history, rapid changes, empty days and normal-navigation fallbacks remain coherent.
- Mobile search filters use a native disclosure and a View plans anchor. Fixed a hash-navigation bug that initially stole focus from that anchor, and a transition overlay that initially intercepted rapid clicks.
- Organizer forms have a live draft card, safe literal-text updates, subtle category-art transitions and an unsaved state. Draft artwork, validation responses and published sample artwork agree. Existing artwork survives unrelated edits.
- Organizer cards show registration/capacity counts. Ticket title, date, location and paid reference appear before artwork. Pending/cancelled tickets have distinct styling; cancellation actions name their result clearly. Admin reports become readable stacked rows on phones.

### Fresh research and what informed the revision

Reviewed October 6, 2026. Product observations describe the inspected public interfaces; the new interactions are original BroncosHub adaptations.

| Primary source | What informed the work |
| --- | --- |
| [Luma Discover](https://luma.com/discover) and [official discovery guidance](https://help.luma.com/p/searching-for-events) | Visually inspected compact event cards/rows and readable metadata; guidance confirms date/location/category-based discovery. Adapted that focus to choosing a day on one campus. |
| [Partiful Explore](https://partiful.com/explore) | Visually inspected expressive posters with readable titles and stable event details. Retained original BroncosHub artwork while giving practical information more space. |
| [USWDS typography](https://designsystem.digital.gov/components/typography/) | Its guidance favors effective 16px body text and sparing use of smaller text. The revised shared scale is our design choice, not a claimed universal WCAG minimum. |
| [GOV.UK type scale](https://design-system.service.gov.uk/styles/type-scale/) and [text inputs](https://design-system.service.gov.uk/components/text-input/) | Readable labels, persistent hints and clear form controls informed larger fields and stronger contrast. |
| [Linear’s UI redesign](https://linear.app/now/how-we-redesigned-the-linear-ui) | The March 2024 article describes improving hierarchy/contrast and testing real interface states. Applied that process across student, organizer and account views. |
| [MDN View Transition API](https://developer.mozilla.org/en-US/docs/Web/API/View_Transition_API) and [reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion) | Scoped transitions to date selection/results and respected user motion preferences, with normal links as the foundation. |

### Verified

- **30 isolated preview tests pass**: all baseline flows, exact-day filters, day counts, other-filter preservation, invalid/empty/out-of-range dates, cancelled events, and draft/published poster consistency.
- All three JavaScript files pass syntax checks; Python compilation and Git whitespace checks pass.
- Agents visually checked every student route/state and organizer/account/admin screen at desktop size. Root rechecked changed layouts at 390px and 320px, including signup, tickets, event editor, profile, dashboard, statistics, reports and discovery; tested pages fit without page-level horizontal overflow. The date rail intentionally scrolls horizontally.
- Browser checks passed for rapid successive date clicks, keyboard selection/focus, typed filters, browser Back, mobile jump-link focus and reduced-motion selection with no View Transition or smooth scrolling.
- With the application’s JS assets deliberately blocked, native date links navigated correctly and filter controls remained open/usable. This tests progressive enhancement; it is not a claim about every browser.
- Live draft title/date/location/price/category changes, literal HTML-looking text and original-art restoration checked in Chrome. Ticket confirmation dialogs retained safe initial focus and explicit actions.
- Final remote fetch still shows main at `2d510a48dc9056ebf20dfdce01af53132da8647a`. No application factory, backend models/routes/services or teammate tests were changed.

## Remaining team integration

The preview remains separate from the unfinished application. Authentication, production authorization, database state and payments require Jaden and Patrick’s implementations. Date-rail and draft-card context contracts are in `ui-integration.md`. Sample checkout fields remain disabled and fictional. These are bounded UI checks, not complete accessibility certification or production readiness. No push, merge, PR or deployment occurred.

## Calendar and dropdown follow-up — October 6, 2026

The calendar now has previous/next week links with a short directional transition. Browsing weeks preserves the selected date and other filters; selecting a day changes results. The mobile rail scrolls horizontally, and exact-date links reveal the selected day after loading. Shared native select controls use a chevron inset 16px from the edge with 48px text padding; forced-colors restores the system arrow.

Two agents contributed the select styling and a read-only calendar review. Review findings about fast Back/Forward requests, first-week form state and offscreen mobile selections were fixed. All 34 preview tests and JS syntax/whitespace checks passed. Browser verification covered desktop, 390px and 320px layouts, later-date selection, filter preservation, Back/Forward under delayed responses, horizontal scrolling, reduced motion, forced colors, and normal navigation with application scripts blocked. Temporary browser overrides were restored. Screenshot: `screenshots/calendar-controls-v3.jpg`. Changes remain local.

## Cover upload follow-up — October 6, 2026

Added optional cover-image upload to create/edit, with native file selection, drag/drop handlers, image crop preview, filename, replacement/removal and readable validation errors. The chosen cover appears throughout event discovery/detail, organizer and registration UI. Rejected replacements can be dismissed while keeping a valid existing cover, and removal survives unrelated form errors.

Two agents implemented the isolated upload service/tests and reviewed the UI logic. All 43 preview tests passed, including nine cover tests; JS syntax and whitespace checks passed. Browser checks verified file selection on desktop and 320px/390px layouts, publish/display, category preservation, rejected-file recovery and upload/removal through validation retries. The browser tool does not support simulating OS file drag/drop, so that handler was code-reviewed but the drag gesture was not exercised. No production application modules changed; image storage remains temporary local preview storage.

## Profile redesign — October 6, 2026

Replaced the tall form and sparse sidebar with a full-width campus identity banner,
a compact details panel, and the next confirmed event. The banner shows saved name,
initials, role, optional year, and an actual upcoming count from preview registrations.
Email is selectable static information. Save/Discard and an unsaved status make the
editing state explicit; the large demo warning is now a small readable note.
Account shortcuts and sign-out sit below both panels. Supporting copy stays at least
14px, with 16px inputs and shared inset dropdown arrows.

Two agents helped review the layout and implement/test registration summaries.
All 50 preview tests pass, including seven summary tests for valid tickets,
cancellation, payment, past events, empty states, validation and role boundaries.
Browser checks verified saved identity remains unchanged during edits, Discard,
error focus and recovery, successful saves and the next-ticket link. Desktop,
820px, 390px and 320px layouts were visually checked with no page overflow.
JS syntax and Git whitespace checks pass. Screenshot: `screenshots/profile-v2.jpg`.
The production backend is unchanged; profile data in this runner remains fictional
and session-scoped. Changes remain local and unpushed.
