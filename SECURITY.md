# Security Policy

## Reporting a Vulnerability

Please report suspected vulnerabilities privately through
[GitHub Security Advisories](https://github.com/Von-Van/vieweratlas/security/advisories/new).
Do not open a public issue containing credentials, private data, or exploit
details.

Include the affected component, reproduction steps, impact, and any proposed
mitigation. Reports will be acknowledged as availability permits.

## Deployment Expectations

ViewerAtlas is a portfolio project and is not currently offered as a hosted
service. Operators deploying it are responsible for:

- keeping raw presence data and optional raw VOD artifacts private;
- exposing only the aggregate `data/frontend-data.json` payload;
- storing Twitch credentials in a secret manager, never in browser code;
- reviewing IAM, network, retention, budget, and CDN security-header settings;
- running the included CI, dependency audits, smoke tests, and rollback checks;
- reviewing Twitch's terms and applicable privacy requirements.

## Automated Checks

The repository runs Python dependency auditing, Bandit static analysis, npm
auditing, tests, frontend type checking, build verification, shell syntax
checks, and AWS JSON validation on pull requests and pushes to `main`.
