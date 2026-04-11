# Project instructions

## API source of truth
Use `docs/remnawave-openapi.json` as the source of truth for all Remnawave API integration work.

## Requirements
- Do not invent endpoints.
- Infer request and response shapes from the OpenAPI schema.
- Include bearer token support for endpoints that require Authorization.
- Prefer typed models and reusable API client helpers.
