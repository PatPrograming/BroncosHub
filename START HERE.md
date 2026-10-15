# BroncosHub

BroncosHub's UI and local preview live in this repository.

## Try the working organizer dashboard and moderation

Double-click **Start Organizer Demo.command**, then open **http://localhost:5075/demo**.
Click an event's **Statistics** button to see confirmed signups, remaining spaces,
and the year-level breakdown. The look matches the existing UI.

Use **Moderation** in the top demo bar, or open **http://localhost:5075/demo/admin**,
to review reports, dismiss a concern or remove an event. Each decision saves a
reason and review history. **Report an event** switches to a fictional student
so you can try sending a report into the queue. Removing an event cancels its
active registrations and updates the organizer's totals; it does not issue refunds.

This runs the real organizer routes with Jaden's Event and Registration models,
using fictional records in a separate temporary database. It resets when you
stop the server and does not touch your real account or saved events. Keep its
Terminal window open while testing. Payments and event creation/editing are
still being connected, so their actions aren't offered in this demo.

## See the website

On macOS, double-click **Start Preview.command** in this folder. Keep its Terminal window open, then visit:

**http://localhost:5074**

`localhost` means your computer. `5074` is just the preview's port number.

For other systems, use the Python setup steps in
[UI integration](event-hub/docs/ui-integration.md#run-the-preview).

Use **View as Student** at the top of the site to switch between student, organizer, admin and guest screens. Guest view has the login and account registration pages. The preview includes fictional events and demo actions. It does not create accounts or charge payments. Reset sample data restores the starting examples.

In **Create an event → Cover image**, choose a JPG, PNG or WebP up to 5 MB.
You can preview, replace or remove it. Demo uploads stay on your computer and
clear when the preview resets or its server restarts; permanent storage is part
of the team's backend integration.

## Where everything lives

- `event-hub/app/templates/`: every page and shared UI components.
- `event-hub/app/static/css/style.css`: colors, layout, responsive styles and motion.
- `event-hub/app/static/js/main.js`: navigation and confirmation dialogs.
- `event-hub/app/static/images/`: original event poster artwork.
- `event-hub/tools/`: isolated sample preview and its tests.
- `event-hub/docs/ui-plan.md`: page structure, design decisions and research links.
- `event-hub/docs/ui-integration.md`: handoff notes for Jaden and Patrick.

Patrick's Google sign-in, Jaden's Event/Registration models, and the organizer
dashboard/statistics and event moderation are connected in the real app. Public
event browsing, event registration and payments still need integration. The
report form works, but its link needs connecting when the public event page is ready.
The older port 5074 preview
remains a sample walkthrough of all screens. Running either preview does not
deploy the website.
