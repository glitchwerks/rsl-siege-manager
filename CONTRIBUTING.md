# Contributing to Siege Assignment Web App

Outside contributions are welcome. This document covers how to set up a local environment, the project's branching and commit conventions, how to run tests and linters, and what to expect from the review process.

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

---

## Setting up a local environment

Follow the **Quick Start** and **Dev Mode** sections in [README.md](README.md). Those sections are the authoritative reference for getting a working local stack — this file does not duplicate them.

---

## Branching conventions

- **Never commit directly to `main`.**
- All work goes on a feature branch: `feat/<name>`, `fix/<name>`, `docs/<name>`, `ci/<name>`, etc.
- Open a PR against `main` when the work is ready for review.
- For large features that span multiple work streams, cut a primary feature branch off `main` and use sub-branches (e.g. `feature-ux`, `feature-ux-api`, `feature-ux-frontend`). Sub-branch PRs merge into the primary branch; the primary branch PR merges into `main`.

---

## Commit message style

This repo uses a Conventional-Commits-adjacent style. Match the format in `git log --oneline`:

```
feat(scope): short imperative description
fix(scope): short imperative description
docs: short description
ci: short description
refactor(scope): short description
```

- **Type** is required: `feat`, `fix`, `docs`, `ci`, `refactor`, `test`, `chore`.
- **Scope** is optional but helpful for service-specific changes: `(auth)`, `(frontend)`, `(bot)`, etc.
- Subject line is lowercase, no trailing period, 72 characters or fewer.
- Body is optional; use it to explain *why*, not *what*.

---

## Running tests

### Backend

```bash
cd backend
pip install -r requirements-dev.txt
pytest --ignore=tests/test_schema.py -v
```

`test_schema.py` requires a live database and is excluded from the standard run.

### Frontend

```bash
cd frontend
npm ci
npm run build
```

There is currently no separate `npm test` command — the build serves as the integration check. Type errors and lint failures fail the build.

### Bot

```bash
cd bot
pip install -r requirements-dev.txt
pytest
```

---

## Running linters

### Backend

```bash
cd backend
black .
ruff check .
ruff check . --fix   # auto-fix where possible
```

### Frontend

```bash
cd frontend
npx eslint src/
npx prettier --write src/
```

Or use the npm script shorthand:

```bash
cd frontend
npm run lint
```

### Optional: local pre-commit hooks via `prek`

[`prek`](https://prek.j178.dev/) is an opt-in, Rust-based, pre-commit-config-compatible hook
runner published to PyPI as a binary wheel. CI remains the authoritative lint and test gate;
using `prek` does not change CI requirements.

Install it with this repository's preferred tool, or use `pip` or `pipx`:

```bash
uv tool install prek   # recommended
# or: pip install prek
# or: pipx install prek
```

Then register the Git hook once from the repository root:

```bash
prek install
```

**Hard precondition: activate the relevant area's virtual environment
(`backend/.venv` or `bot/.venv`, whichever area you are changing) before running `prek`.**
The backend and bot hooks use `language: system`, so they invoke whichever `black` and `ruff`
binaries appear first on `$PATH`, rather than a specific virtual environment. Without the
correct environment active, `prek run` may fail with a command-not-found error or silently use
versions that differ from CI, causing false passes or failures.

---

## Opening a pull request

1. Fork or branch from `main` (pull the latest first).
2. Make your changes on a feature branch.
3. Ensure all tests pass and linters report no errors.
4. Open a PR against `main`. The PR template will prompt for a summary, linked issue, and test plan — fill it in.
5. CI runs automatically: black + ruff + pytest (backend) and eslint + build (frontend). A green CI run is required before merge.

**What reviewers look for:**

- Tests for any new behavior or bug fix. Modified code without tests will not be merged.
- README updates if the change affects how the project is run, built, or configured.
- Commit messages that follow the style above.
- No unrelated changes bundled into the PR.

---

## Questions

If you are unsure whether something is in scope, open an issue and ask before writing code. See [SUPPORT.md](SUPPORT.md) for where to ask questions.
