# BroncosHub

Campus events for SCU students. Built by Jaden Hill, Patrick Zhang, and Ethan Can for CSEN 174.

The app lives in [`event-hub/`](event-hub). Follow the [setup and testing instructions](event-hub/README.md) to run it with Python 3.12+ at **http://127.0.0.1:5080**. Keep your existing `.env` settings when updating.

Every verified SCU user can host and manage their own events. Admin moderation is separate, and paid events require Stripe setup. The free hosting configuration uses Render with Supabase for saved records and private images. Google sign-in credentials, Stripe verification, and the public deployment still need verification; see the [deployment guide](event-hub/docs/deployment.md).

For the local app and fictional-data previews, see [START HERE](START%20HERE.md).
