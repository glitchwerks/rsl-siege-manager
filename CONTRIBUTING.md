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

**Frontend precondition: run `npm ci` in `frontend/` before running `prek`.** The
`frontend-prettier` hook depends on the `prettier-plugin-tailwindcss` plugin declared in
`frontend/package.json`, which lives in `frontend/node_modules`. Without `npm ci` having been
run, the hook fails with a `Cannot find module` error; that failure means the prerequisite was
not met, not that there is a real problem with the code being committed.

**`.ps1` files and CRLF — verified safe, no `.gitattributes` change needed (issue [#512](https://github.com/glitchwerks/rsl-siege-manager/issues/512)).**
`.gitattributes` forces `eol=lf` on `*.sh .py .ts .tsx .yml .yaml .json .md .txt`, but not on
`*.ps1`. On this Windows checkout (`core.autocrlf=true`), `.ps1` files under `scripts/` are
genuinely CRLF on disk (confirmed with `file scripts/generate-origin-pfx.ps1` →
`... with CRLF line terminators`), unlike the extensions above which are normalized to LF on
checkout.

Running `trailing-whitespace --all-files` and `end-of-file-fixer --all-files` individually
(a combined `prek run --all-files` errors out first on `backend-black`/`bot-black` unless the
relevant venv is active per the precondition above, so each hook was run separately) against
every tracked file produced zero modifications to any of the 8 tracked `.ps1` files:

```
$ prek run trailing-whitespace --files scripts/*.ps1 scripts/tests/generate-origin-pfx.Tests.ps1
trim trailing whitespace.................................................Passed

$ prek run end-of-file-fixer --files scripts/*.ps1 scripts/tests/generate-origin-pfx.Tests.ps1
fix end of files.........................................................Passed
```

That "Passed" only proves the existing `.ps1` files had nothing to fix, not that a rewrite
would preserve CRLF — both hooks short-circuit without writing a byte when the file already
satisfies the rule. To test the mutation path, a scratch probe file was written with CRLF line
endings, trailing spaces, and no final newline, then fed through each hook directly, with
`od -c` diffing the bytes before/after:

```
$ printf 'Write-Output "a"\r\nWrite-Output "b"   \r\n' > scripts/_crlf-probe.ps1
$ prek run trailing-whitespace --files scripts/_crlf-probe.ps1
trim trailing whitespace.................................................Failed
  Fixing scripts/_crlf-probe.ps1
# od -c after: trailing spaces stripped, \r\n preserved on both lines

$ printf 'Write-Output "a"\r\nWrite-Output "b"' > scripts/_crlf-probe.ps1
$ prek run end-of-file-fixer --files scripts/_crlf-probe.ps1
fix end of files.........................................................Failed
  Fixing scripts/_crlf-probe.ps1
# od -c after: file now ends "...b" + \n (bare LF, not \r\n)
```

(Probe file deleted after the test; not part of this change.)

This confirms, with an actual rewrite rather than only no-op passes, why `.ps1` is safe:
`pre_commit_hooks/trailing_whitespace_fixer.py` detects each line's existing terminator
(`\r\n` vs `\n`) and re-appends whichever one it found — it never normalizes CRLF to LF.
`pre_commit_hooks/end_of_file_fixer.py` treats both `\r` and `\n` as valid trailing
line-break bytes when deciding whether a file already ends cleanly, but when it *does* need to
append a missing terminator (line 21 of that file) it always writes a bare `\n`. On a CRLF
file that is otherwise missing its final newline, this produces one line with a different EOL
than the rest of the file. In this repo that is harmless in practice — `core.autocrlf=true`
normalizes the file back to consistent CRLF on the next checkout, and none of the 8 tracked
`.ps1` files were missing a final newline to begin with — but it is the one caveat to the
"neither hook touches CRLF" claim above.

A repo-wide `trailing-whitespace --all-files` / `end-of-file-fixer --all-files` pass on this
checkout modified only 3 unrelated pre-existing files (`CLAUDE.md`, `docs/siege_levels.md`,
`scripts/excel-import/requirements.txt` — each missing a final newline or carrying a stray
trailing blank line, already covered by the existing `.md`/`.txt` `eol=lf` rules and unrelated
to CRLF; reverted, out of scope for issue #512, tracked separately). No other extension
missing from the `eol=lf` list exhibited a CRLF-driven ghost modification either.

Conclusion: no `.gitattributes` or `.pre-commit-config.yaml` change was needed for `.ps1`
files. (The pre-existing `detect-private-key` exclusion for
`scripts/tests/generate-origin-pfx.Tests.ps1` in `.pre-commit-config.yaml` is a separate,
unrelated false-positive fix from PR #509 and is untouched by this verification.)

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
