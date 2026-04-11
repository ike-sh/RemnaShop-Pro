## Required bootstrap behavior
The repository must keep a single bootstrap entrypoint.

The bootstrap implementation must support:
- install
- uninstall

A fresh Debian or Ubuntu server with only curl available must be able to start installation successfully.
The bootstrap script must automatically install git if it is missing.
Do not leave git as a manual prerequisite.

During install, the bootstrap script must collect required environment values interactively, especially:
- ADMIN_ID
- BOT_TOKEN

The script must write these values into .env automatically.
Do not leave ADMIN_ID and BOT_TOKEN as a normal manual post-install requirement.

Install must only report success after the remnashop container becomes healthy.
It must not report success merely because docker compose up -d returned successfully.

Uninstall must only remove RemnaShop-Pro resources and must never affect unrelated Docker Compose projects on the host.
Do not use broad Docker cleanup commands.

If documentation changes, the actual script behavior must be updated to match.
Do not solve bootstrap tasks with documentation-only changes.
