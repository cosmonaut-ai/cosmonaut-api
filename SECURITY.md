# Security Policy

Please do not report security issues in public GitHub issues.

Report vulnerabilities or accidental secret exposure to `support@cosmonaut-ai.com`. Include the affected repository, file path, relevant commit or release, reproduction details, and impact if known.

## Scope

This policy covers the Cosmonaut API service, worker code, deployment configuration, and documentation in this repository.

## Secret Handling

- Do not commit `.env`, `.envrc`, generated `client-config.json`, AWS credentials, GCP service account keys, Stripe secrets, ElevenLabs keys, Sentry auth tokens, or local agent workspaces.
- Runtime secrets belong in AWS SSM Parameter Store. CI/CD-only credentials belong in GitHub Actions secrets. Local overrides belong in ignored local files.
- Public Cognito IDs, Cognito domains, Sentry DSNs, and analytics project tokens are client-visible configuration, not server-side secrets. They should still be documented and scoped to the correct environment.

## Public Contributions

When opening a pull request, scrub logs, stack traces, screenshots, and test fixtures for tokens, account IDs that are not already public configuration, private customer data, and unpublished generated story content.
