# ViewerAtlas Deployment Guide

This is the entry point for deploying the current ViewerAtlas release.

The website, S3 buckets, CloudFront distribution, and Virginia VPC created by
the original setup do **not** need to be created again. The critical update
changes only the Twitch collector, its private data format, AWS task settings,
monitoring, and schedules.

Follow the current step-by-step guide here:

**[Open the current deployment guide](twitchiobot/docs/DEPLOYMENT.md)**

It is written for a non-technical operator and includes:

1. checking Docker, AWS sign-in, `.env`, subnets, and security group;
2. authorizing the dedicated Twitch bot with `user:read:chat`;
3. deploying while all automatic schedules remain paused;
4. running a five-channel test, a 100-channel batch, and then one complete
   1,200-channel survey;
5. confirming the AWS alarm-email subscription;
6. enabling surveys only after all three rollout checks pass;
7. verifying the 6:00 AM, 2:00 PM, and 10:00 PM Eastern survey schedule and
   the 1:00 AM Eastern analysis schedule;
8. proving the overnight analysis refreshed both outputs after the newest
   completed survey and checking the public-data privacy boundary; and
9. pausing with the schedule-only emergency helper, resuming, and rolling back
   safely.

A production survey listens to up to 1,200 channels in twelve strict batches.
Every successfully joined channel in a batch receives the same five-minute
window. Allow roughly **80 minutes** for a full survey; a two-hour application
timeout is the safety limit.

Do not reuse instructions from an older copy that mention Twitch IRC,
`TwitchIO 2.8`, `chat:read`, `main.py collect`, `main.py continuous`, two
separate Twitch secrets, a constantly running collector service, SQS workers,
or a 3:00 UTC analysis rule. Those paths are retired.

For routine checks after deployment, use
**[Daily Operations](twitchiobot/docs/DAILY_OPERATIONS.md)** and the
**[deployment checklist](twitchiobot/docs/deployment-checklist.md)**.
