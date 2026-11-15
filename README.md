# Siege Assignment Web App

[![CI](https://img.shields.io/github/actions/workflow/status/glitchwerks/rsl-siege-manager/ci.yml?branch=main&label=CI&logo=github)](https://github.com/glitchwerks/rsl-siege-manager/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/glitchwerks/rsl-siege-manager?label=release&logo=github&color=blue)](https://github.com/glitchwerks/rsl-siege-manager/releases/latest)
[![Live site](https://img.shields.io/badge/live-rslsiege.com-success?logo=cloudflare&logoColor=white)](https://rslsiege.com)

Siege Manager helps Raid: Shadow Legends clans plan siege assignments, catch placement errors, and share the finished plan through Discord. Try the [live site](https://rslsiege.com) or read its [usage guide](https://rslsiege.com/guide).

## Features

- **Assignment checks** flag overlaps, capacity errors, and misplacements before posting.
- **Auto-fill** previews assignments for open positions before committing them.
- **Attack-day planning** accounts for pinned members across siege days.
- **Discord integration** provides sign-in, assignment images, and tracked notifications.
- **Self-hosting** works with Docker and a bundled bot or a compatible external sidecar.

## Start developing

Install Docker, then start a local demo with seeded members and siege data:

```bash
git clone https://github.com/glitchwerks/rsl-siege-manager.git
cd rsl-siege-manager
cp .env.example .env
docker compose up --build
```

Open [localhost:5173](http://localhost:5173). The example configuration disables authentication for local development; do not expose that demo to other users. Stop it with `docker compose down`. To delete the demo database volume as well, run `docker compose down -v`.

For backend and frontend hot reload, start PostgreSQL with Docker, then use [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 20:

```bash
docker compose up postgres

# In a second terminal
cd backend
uv sync --frozen --dev
uv run alembic upgrade head
uv run python scripts/seed_demo.py
uv run uvicorn app.main:app --reload

# In a third terminal, from the repository root
cd frontend
npm ci
npm run dev
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests, linting, and migrations. The
[example environment](.env.example) lists the values needed for real Discord
OAuth. The [self-host guides](https://github.com/glitchwerks/rsl-siege-manager/wiki)
cover deployment options and their configuration.

## Releases

The current production site runs on a guarded VPS deployment. A Git tag alone does not deploy it. See [RELEASING.md](RELEASING.md) for the release process and the [VPS release runbook](docs/operations/vps-release-workflow.md) for operator gates. The older Azure production deployment workflows are disabled.

## License and contributing

This project is available under the [MIT License](LICENSE). Contributions are welcome; read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request.
