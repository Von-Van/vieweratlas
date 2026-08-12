# ViewerAtlas Alert Email

The current setup is automatic. Put the recipient in `ALERT_EMAIL` inside the
AWS `.env` file, then run:

```bash
./apply-monitoring.sh
```

The script creates or reuses the `vieweratlas-alerts` SNS topic, subscribes the
configured email address once, and connects the current survey and analysis
alarms. AWS
sends a confirmation message to a newly subscribed address. Open that message
and choose **Confirm subscription**; alarms cannot send email before that step.

Current alarms cover a missing terminal survey, a partial survey, a spike in
collector errors, an explicit analysis failure, and a missing successful daily
analysis. The retired VOD and continuous-collector alarms are not part of this
release.

Use [the deployment guide](../../docs/DEPLOYMENT.md) for the complete setup and
[daily operations](../../docs/DAILY_OPERATIONS.md) for routine checks.
