# Security Policy

## Supported Versions

This repository is currently intended as a public demo project. Security fixes are applied to the latest `main` branch.

## Reporting a Vulnerability

Please open a private security report through GitHub Security Advisories, or contact the repository owner directly.

## Secret Handling

- API keys and secrets must be provided through environment variables.
- `.env` is ignored by git and must never be committed.
- Browser-exposed API keys are not required for the default demo flow.
- Paid routing calls are server-side only and can be limited with `GOOGLE_MAPS_DAILY_CALL_LIMIT`.

## Operational Security Notes

- If a key is ever exposed, rotate it immediately in the provider console.
- Restrict production keys by source IP/domain and API scope.
- For deployments, run with `DJANGO_DEBUG=false` and a strong `DJANGO_SECRET_KEY`.
