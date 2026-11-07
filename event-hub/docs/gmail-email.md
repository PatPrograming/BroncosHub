# BroncosHub Gmail sender

Status checked November 7, 2026: the email implementation is deployed, and `broncoshubscu@gmail.com` authorized send-only access through the separate `broncoshub-email` project. One approved setup message was accepted by Gmail; the sender's Sent folder confirmed the From and Reply-To addresses, and the recipient reported receiving it in Spam. This verifies delivery of that local transport test, not reliable inbox placement or a hosted registration flow. Student Google login and hosted event-email checks remain separate work.

## Create and authorize the sender

1. Create a dedicated free Gmail account for BroncosHub. The account owner completes the password, recovery information, verification and Google agreements. No domain purchase or Google Workspace subscription is needed for an `@gmail.com` sender.
2. In Google Cloud, create a separate project for **BroncosHub Email** and enable the **Gmail API**. Do not change Patrick's student-login project, its credentials or its authorized callback. Standard Gmail API use does not require purchasing a paid quota upgrade.
3. Configure Google Auth Platform with an External audience and only `https://www.googleapis.com/auth/gmail.send`. The app may send email; it may not read, modify or delete mail. The person authorizing the sender must select the dedicated BroncosHub account. Recipients do not authorize access to their inboxes.
4. Publish the sender OAuth app to **In production** before its final authorization. External apps left in Testing issue Gmail refresh tokens that expire after seven days. Google documents a personal-use verification exception for fewer than 100 authorizing users; this one-account setup may show an unverified-app warning. That is not Google approval of the public BroncosHub site. The owner must review the warning/consent, and must not authorize an unfamiliar project. Wider Gmail-account access would require reassessing Google's verification requirements.
5. Create a **Desktop app** OAuth client for the local sender setup tool. Download its client JSON into `instance/deployment/`, which is ignored by Git. Keep it private. This desktop client is different from the web client used to sign students in.
6. Install the one-time setup dependency and run the local helper from `event-hub`, using the downloaded filename and the actual sender address:

```sh
python -m pip install -r tools/requirements-gmail-setup.txt
python tools/authorize_gmail.py --client-secrets instance/deployment/google-client.json --sender YOUR-ACCOUNT@gmail.com
```

The helper prints a Google consent link and listens temporarily on `127.0.0.1`. Open that link in the regular browser, select the sender account and review send-only consent. It uses a random state and PKCE, does not open the browser itself, and never prints credentials. The resulting credentials are saved with owner-only permissions inside `instance/deployment/`. The helper refuses to overwrite an existing credential file. Use its `--help` for the final output-path options.

Send-only permission cannot inspect the selected account's profile or Sent folder. Confirm the account on Google's consent screen, and verify the actual From address in the controlled test message before enabling general delivery.

## Connect Render

Copy the generated `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` and `NOTIFICATION_FROM_EMAIL` into Render's private environment settings. Do not put the credential file into Git, screenshots, group messages or a course submission. Keep `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` unchanged: those belong to student login.

Keep `SUPPORT_EMAIL=ecan@scu.edu` for replies. Deploy the reviewed code with `NOTIFICATION_EMAIL_PROVIDER=disabled` first. Check pending notification counts before enabling Gmail: the worker will process every eligible pending notice, not only a test message. Enable `NOTIFICATION_EMAIL_PROVIDER=gmail` after sender validation and backlog review. The app uses Google's HTTPS API because Render Free blocks ordinary SMTP ports. No Gmail password or app password goes into BroncosHub.

The Blueprint leaves the provider setting under dashboard control (`sync: false`) so a later sync does not reset an approved Gmail configuration. For a new service, explicitly choose `disabled` until setup is complete. Render documents this behavior in its [Blueprint reference](https://render.com/docs/blueprint-spec#prompting-for-secret-values).

An authorization can be revoked or expire after account/security changes. A `gmail_auth_required` failure means the owner needs to reconnect the sender; it is not a reason to retry the password or widen the permission scope.

## Verify delivery

With the account owner's consent, send a controlled message to the team's chosen test recipient. The first transport check can use a minimal local Flask context and the private sender settings without connecting to the production database or creating a student account. Check the real From address, Reply-To address, plain-text content, website link, Sent folder and recipient inbox/spam folder. A local transport check proves Gmail delivery, not delivery from Render.

After real SCU sign-in works, exercise hosted free registration, cancellation, re-registration, event time/location changes and organizer cancellation with controlled real accounts. Do not create fake production users to bypass Google login. Moderation removal must not repeat the removed event's content. Paid confirmation requires an actual verified payment flow; a checkout redirect alone must not trigger it.

Only the dedicated sender account authorizes Gmail access. Students continue to use their SCU Google login. Do not change the app's allowed SCU domain to `gmail.com` to test the sender.

## Limits and delivery status

- Personal Gmail generally allows 500 outgoing messages per day. Quota or abuse controls can temporarily block sending. A cancellation notice to 100 attendees uses 100 emails in addition to their registration confirmations.
- Gmail does not support a documented send idempotency key. Unknown outcomes are held for review instead of automatically resending and risking duplicate messages. A fixed Message-ID is a search/correlation aid, not a duplicate-prevention guarantee.
- `sent` means Google's API accepted the request. It does not prove inbox delivery. The app cannot inspect the mailbox with its send-only permission.
- The first real test reached Spam. A recipient can mark a trusted message **Not spam** or add the sender to contacts; this does not establish reliable delivery to other students. Do not promise inbox placement. The exact filtering reason was not verified. [Google's guidance](https://support.google.com/mail/answer/1366858?hl=en)
- Explicit rejection can retry with bounded delays, up to eight attempts and 23 hours from notice creation. Expired/unknown messages need attention; the app does not promise to eventually deliver every message after a quota block.
- The notification worker sleeps with the Render Free website. Gmail does not fix that hosting delay. Use this setup for a small pilot with the website's My events page as the current source of registration and event status.

## Official sources checked November 6, 2026

- [Google Desktop OAuth flow](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Gmail API permissions](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Refresh token expiration](https://developers.google.com/identity/protocols/oauth2)
- [Verification exceptions](https://support.google.com/cloud/answer/13464323?hl=en)
- [Gmail API send method](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)
- [Gmail API error handling](https://developers.google.com/workspace/gmail/api/guides/handle-errors)
- [Gmail API limits](https://developers.google.com/workspace/gmail/api/reference/quota)
- [Personal Gmail sending limits](https://support.google.com/mail/answer/22839?hl=en)
- [Render Free limits](https://render.com/docs/free)
