# Cosmonaut API Documentation

This directory contains API contracts, implementation notes, and older design specifications for the backend service.

## Current References

- [`api-spec.md`](api-spec.md): REST and streaming API behavior, authentication, and DTO examples.
- [`audio-implementation.md`](audio-implementation.md): text-to-speech voice listing, audio generation, quota handling, and frontend integration notes.
- [`frontend-world-options.md`](frontend-world-options.md): world creation option fields consumed by frontend clients.
- [`soundtrack-playlist-plan.md`](soundtrack-playlist-plan.md): planned world/session soundtrack playlist assignment behavior.
- [`tech-spec.md`](tech-spec.md): system design notes for the generation loop, DynamoDB schema, and vector memory strategy.

## Product Background

- [`project-description.md`](project-description.md): high-level product goals and narrative consistency requirements.

## Maintenance Notes

- Treat `api-spec.md` as the source of truth for public client behavior unless generated OpenAPI output is added later.
- Keep examples free of real user data, production identifiers that are not public config, and raw provider credentials.
- If an implementation guide becomes historical, mark it clearly at the top instead of leaving stale instructions mixed with current setup steps.
