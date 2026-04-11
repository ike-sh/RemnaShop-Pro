## Required bootstrap UX
The bootstrap install experience should be Chinese for end users.

During install, only these values are required interactively:
- ADMIN_ID
- BOT_TOKEN

Other .env values must remain optional during bootstrap install and must not block deployment.

## Production image contents
The production image must exclude non-runtime repository files, including:
- tests/
- docs/
- README.md
- AGENTS.md
- LICENSE
- .gitignore
- .env.example

Keep these files in the repository, but do not include them in the production image.
