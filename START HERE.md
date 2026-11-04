# BroncosHub

## Open the actual app

On this Mac, double-click **Start BroncosHub.command** and open **http://127.0.0.1:5080**. Keep its Terminal window open while using the app. Press Control+C to stop it.

For a fresh computer, follow [the setup instructions](event-hub/README.md#run-it-locally) first. The app needs Python 3.12 or newer.

This is the real app with saved accounts, events, registrations, profiles and cover images. It has organizer statistics, event moderation, and organizer-owned Stripe payment code. Google credentials and Stripe setup still need to be connected; paid events stay disabled until payment setup is complete.

[Hosting and Stripe instructions](event-hub/docs/deployment.md) explain the remaining account setup and sandbox checks. The repository includes a Render deployment configuration; it has not created or paid for hosting.

## Files

- `event-hub/app/templates/`: UI pages and shared components.
- `event-hub/app/static/`: styles, scripts and artwork.
- `event-hub/app/routes/` and `event-hub/app/services/`: application behavior.
- `event-hub/tests/`: automated checks, including the existing Lab 5/6 work.
- `event-hub/instance/`: private local database and uploaded covers; excluded from Git.

The older **Start Preview.command** (5074) and **Start Organizer Demo.command** (5075) open fictional development previews. Use **Start BroncosHub.command** for the actual app.
