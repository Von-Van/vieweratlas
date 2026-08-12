# ViewerAtlas Setup for a Non-Technical Helper

ViewerAtlas already has its website and AWS foundation. This update replaces
the old Twitch chat collector. You do not need to understand the code, rebuild
CloudFront, or recreate any storage bucket.

Use the detailed **[ViewerAtlas deployment guide](twitchiobot/docs/DEPLOYMENT.md)**
and complete each section in order. If a command prints `[ERROR]`, stop there
and give the project owner the complete error text. Do not guess a replacement
value and never paste a password or token into a message.

## What you will be doing

- Confirm Docker Desktop is running and the AWS sign-in works.
- Check the existing `.env` file for the Virginia bucket and network IDs.
- Use a guided browser sign-in to authorize the dedicated ViewerAtlas Twitch
  account. The program requests only permission to read chat messages.
- Deploy the update with its schedules paused.
- Run a small test, one full 100-channel test, and then one complete
  1,200-channel survey. The last test takes roughly 80 minutes.
- Open the AWS notification email and click **Confirm subscription** so alarm
  email can reach the project owner.
- Turn on three daily surveys and the overnight analysis only after all three
  tests pass.

The five Twitch credential parts—Client ID, Client Secret, bot account ID,
access token, and refresh token—are stored and rotated together as one private
JSON secret in AWS Secrets Manager. Do not put any of them in `.env`, GitHub,
this guide, email, or a support ticket.

## What “normal” looks like

- The collector is stopped most of the day. AWS starts a temporary task at
  6:00 AM, 2:00 PM, and 10:00 PM Eastern.
- A full run takes roughly 80 minutes. A two-hour safety limit stops an
  unhealthy run.
- The overnight analysis begins at 1:00 AM Eastern, after the 10:00 PM survey.
- It records each unique person who actively writes a message during the
  five-minute sample. It does not record lurkers or message text.
- The website continues showing its last valid dataset if collection is paused.
- The old IRC collector, SQS workers, and VOD collector stay off.

## If you need to stop

From `twitchiobot/infrastructure/aws`, pause future surveys with:

```bash
./pause-schedules.sh survey
```

This leaves the website alone. For a release failure, run:

```bash
./rollback.sh
```

Rollback disables the new schedules; it does not restart the known-broken old
collector.

The project owner can use
**[Daily Operations](twitchiobot/docs/DAILY_OPERATIONS.md)** after handoff.
