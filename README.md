# ViewerAtlas

ViewerAtlas is a production-oriented Twitch community intelligence project.
It continuously collects chat presence data, detects audience-overlap communities, and publishes analysis artifacts for downstream reporting.

## Project Scope

- Live and VOD Twitch chat collection
- Viewer-overlap graph construction and community detection
- Local development workflow + AWS production runtime
- Script-first operations with explicit guardrails (deploy, schedules, smoke tests, monitoring)

## Production Goals

- Deterministic deployments based on immutable image tags
- Idempotent AWS provisioning/update behavior
- CI and security gates before merge/deploy
- Operational runbook coverage for incident response and rollback
- Documented data handling and retention expectations

## Repository Structure

- `twitchiobot/`: core app, tests, infrastructure scripts, docs
- `setup.sh`: local setup helper
- `.github/workflows/`: CI, security, and deploy preflight checks

## Fast Start

### Local

```bash
cd twitchiobot
python -m pip install -r src/requirements.txt
pytest -q
python src/main.py analyze
```

### AWS (Production)

```bash
cd twitchiobot/infrastructure/aws
./safe-deploy.sh
./create-schedules.sh
./apply-monitoring.sh
./smoke-test.sh
```

See `twitchiobot/docs/DEPLOYMENT.md` for full setup and verification steps.

## Documentation

- App overview: `twitchiobot/README.md`
- Deployment: `twitchiobot/docs/DEPLOYMENT.md`
- Runbook: `twitchiobot/docs/RUNBOOK.md`
- Data policy: `twitchiobot/docs/DATA_POLICY.md`
