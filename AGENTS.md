# Project instructions

## Goal
This repository must support only Docker Compose deployment.
Remove or replace any standalone server-install deployment flow, scripts, docs, and references.

## Source of truth
Use `docs/remnawave-openapi.json` as the source of truth for Remnawave API integration work.

## Constraints
- Preserve existing app behavior unless a change is required for Docker Compose-only deployment.
- Do not keep two parallel installation methods.
- Prefer small, reviewable changes over broad rewrites.
- Update docs, scripts, and environment examples to match the new Docker Compose-only setup.
- If legacy install scripts are no longer needed, remove them cleanly.
- Keep secrets out of the repository.

## Deliverables
- A single supported Docker Compose deployment path
- Updated README and installation docs
- One-command install or bootstrap flow for a fresh Linux server
- Validation steps showing how to start and verify the stack
