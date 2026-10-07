# BroncosHub

BroncosHub's UI and local preview live in this repository.

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

The production framework, account behavior and database remain the team's work to connect. Before sharing changes, review the diff and the latest remote commits. Running the preview does not deploy the website.
