# ViewerAtlas (Application)

ViewerAtlas collects Twitch audience-presence data and builds community graphs from viewer overlap.

## Goals

- Produce repeatable community analysis outputs from live and VOD chat presence
- Run locally for iteration and in AWS for always-on production collection
- Keep operational behavior explicit, scriptable, and auditable

## Runtime Model

- `vieweratlas-collector` runs as an always-on ECS service
- `vieweratlas-analysis` runs as a scheduled ECS task
- `vieweratlas-vod-collector` runs as a scheduled ECS task
- Immutable image tags are authoritative for deployments

## Project Layout

- `twitchiobot/src`: pipeline, collectors, storage, orchestration
- `twitchiobot/tests`: pytest suite
- `twitchiobot/config`: default YAML config and env examples
- `twitchiobot/infrastructure/docker`: Dockerfiles
- `twitchiobot/infrastructure/aws`: deployment, scheduling, smoke test, monitoring scripts
- `twitchiobot/docs`: deployment, runbook, policy, developer docs

## Local Development

```bash
cd twitchiobot
python -m pip install -r src/requirements.txt
cp config/.env.example .env
cp channels.example.txt channels.txt
```

Run tests:

```bash
pytest -q
```

Run analysis locally:

```bash
python src/main.py analyze
```

Run collection locally:

```bash
# Requires both values for collect/continuous modes
export TWITCH_OAUTH_TOKEN=...
export TWITCH_CLIENT_ID=...
python src/main.py collect
```

## AWS Deployment

Use the script-first flow in `twitchiobot/infrastructure/aws`:

1. `safe-deploy.sh` or `deploy.sh`
2. `create-schedules.sh`
3. `apply-monitoring.sh`
4. `smoke-test.sh`

Detailed instructions: `twitchiobot/docs/DEPLOYMENT.md`

## Operational Documents

- Deployment guide: `twitchiobot/docs/DEPLOYMENT.md`
- Production runbook: `twitchiobot/docs/RUNBOOK.md`
- Data policy: `twitchiobot/docs/DATA_POLICY.md`
- SNS alert setup: `twitchiobot/infrastructure/aws/SNS_SETUP.md`

## CI/Security Gates

GitHub Actions workflows:
- `.github/workflows/ci.yml`: tests + syntax/parse checks
- `.github/workflows/security.yml`: `pip-audit` + `bandit` high/critical gates
- `.github/workflows/deploy-preflight.yml`: manual secret/variable shape checks
