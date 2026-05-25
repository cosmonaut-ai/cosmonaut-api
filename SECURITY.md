# Security Policy

Please do not report security issues in public GitHub issues.

Report vulnerabilities or accidental secret exposure to `support@cosmonaut-ai.com` with the affected repository, file path, and reproduction details.

## Secret Handling

- Do not commit `.env`, `.envrc`, generated `client-config.json`, AWS credentials, GCP service account keys, Stripe secrets, ElevenLabs keys, Sentry auth tokens, or local agent workspaces.
- Production secrets belong in AWS SSM Parameter Store, GitHub Actions secrets, or local ignored files.
- Public Cognito IDs, domains, Sentry DSNs, and analytics project tokens are not treated as server-side secrets, but should still be documented and scoped.
