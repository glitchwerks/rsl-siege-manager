---
touches:
  - backend/pyproject.toml
  - backend/requirements-dev.txt
  - bot/pyproject.toml
  - frontend/package.json
  - frontend/eslint.config.js
  - frontend/.prettierrc
  - infra/
  - bicepconfig.json
  - .github/workflows/ci.yml
  - .github/workflows/infra-ci.yml
  - .gitattributes
  - CONTRIBUTING.md
skills_relevant:
  - python
  - bicep
  - github-actions
---

# prek Hooks Adoption Plan

**Status:** Phase 0 + Phase 1 shipped — [#508](https://github.com/glitchwerks/rsl-siege-manager/issues/508) / [PR #509](https://github.com/glitchwerks/rsl-siege-manager/pull/509), merged 2026-07-26. Phases 2-4 (frontend hooks, infra hooks, README onboarding) remain planned, not yet implemented.
**Tracking:** [#507](https://github.com/glitchwerks/rsl-siege-manager/issues/507)

## Goal

Adopt `prek` (Rust-based, `pre-commit`-config-compatible hook runner) to give
fast local feedback on the same checks CI already enforces, without
duplicating or replacing the CI gate. `prek` reads the standard
`.pre-commit-config.yaml` format — no new config language, no Python runtime
dependency for the runner itself.

## Current tool inventory (source of truth)

| Area | Format | Lint | Config |
|---|---|---|---|
| `backend/` | `black`, line-length 100, py312 | `ruff`, rules `E,F,I,UP` | `backend/pyproject.toml:1-11` |
| `bot/` | `black`, line-length 100, py312 | `ruff`, rules `E,F,I,UP` | `bot/pyproject.toml:1-10` |
| `frontend/` | `prettier` — **manual only**, `npm run format` (`frontend/package.json:10`), not run in CI | `eslint` (`frontend/eslint.config.js`) | `frontend/.prettierrc`, `frontend/eslint.config.js` |
| `infra/` | — | `az bicep lint` + `az bicep build`, already gated in CI | `.github/workflows/infra-ci.yml:85,91,134`, `bicepconfig.json` |

CI invocations to mirror exactly (`.github/workflows/ci.yml`):
- backend: `black --check app/ tests/` (line 62), `ruff check app/ tests/` (line 65)
- bot: no explicit lint step in `bot-ci` job (lines 114-139) — only `pytest tests/ -v`. `bot/pyproject.toml` defines black/ruff config but CI doesn't currently invoke either for `bot/`. **Flag as an existing CI gap**, not something the hook has to match — the hook can still run black/ruff locally for `bot/` even though CI doesn't.
- frontend: `npm run lint` (line 96, → `eslint .`)
- infra: `az bicep lint --file infra/main.bicep` + per-module loop (`infra-ci.yml:85-92`), `az bicep build` (line 134)

No `.pre-commit-config.yaml` exists today (confirmed via repo-root search). No open issue/PR overlaps this work (checked `gh issue list` / `gh pr list` against `pre-commit`, `prek`, `hooks` — zero hits). Related but non-blocking: [#464](https://github.com/glitchwerks/rsl-siege-manager/issues/464) (migrate backend/bot to `uv`) — if that lands first, hook commands become `uv run black` / `uv run ruff` instead of bare invocations; sequencing note only.

## Candidate hooks per area

### backend/
- `black --check` (mirrors CI exactly)
- `ruff check` (mirrors CI exactly)
- Use `language: system` so the hook shells out to the venv's own `black`/`ruff` (already pinned in `backend/requirements-dev.txt`) rather than a hook-managed duplicate toolchain — keeps one source of truth for tool versions and gives best-effort parity with CI output (not a guarantee — see the venv-binding caveat immediately below).
- Scope: `files: ^backend/`, `exclude: ^backend/alembic/versions/` — belt-and-suspenders only: black/ruff already discover and apply `extend-exclude = ["alembic/versions"]` from `backend/pyproject.toml:4-5` on their own by walking up from the staged file path. The hook-level exclude doesn't add coverage; keep it for defense-in-depth but don't rely on it as the mechanism.
- **[BLOCKING — project-reviewer, 2026-07-24] `language: system` does not bind to any venv.** It invokes whatever binary is first on `$PATH` — not the project's `.venv`. `backend/requirements-dev.txt` pins `black>=24.10`/`ruff>=0.8` (range) while `bot/requirements-dev.txt` pins `black==24.10.0`/`ruff==0.8.3` (exact) — a developer without the right venv active gets an unspecified binary, possibly diverging from what CI installs, only best-effort, never a guarantee of the "matches CI" claim above. **Resolved in Phase 1** (shipped, PR #509): `CONTRIBUTING.md` documents that running `prek` requires the relevant area's venv active. Alternative if that proves unreliable in practice: pin `entry:` to the venv's binary path directly (`./backend/.venv/bin/black` POSIX / `./backend/.venv/Scripts/black.exe` Windows) — more deterministic but needs an OS-conditional entry, deferred unless the venv-active convention fails.

### bot/

- Same shape as backend: `black --check`, `ruff check`, `language: system`, scoped to `files: ^bot/`. Same venv-binding caveat as backend above applies.
- **[CONCERN — project-reviewer, 2026-07-24] Adding these as "new coverage" inverts the plan's own "mirror, don't replace" principle** — `bot-ci` (`ci.yml:114-139`) runs only `pytest tests/ -v`, no lint step, so the hook would be the *only* enforcement surface for bot/ formatting/lint. A contributor without hooks enabled can land badly-formatted bot/ code and CI stays green. **Resolved (Phase 0, shipped in PR #509):** `black --check app/ tests/` + `ruff check app/ tests/` were added to the `bot-ci` job (mirrors the backend job's two steps exactly), so the bot/ hook is now a genuine mirror like every other area instead of an unmatched extension.

### frontend/

- `eslint` — mirrors CI (`npm run lint`). Scope: `files: ^frontend/`.
- `prettier --check` (not `--write`, so the hook fails rather than silently reformatting) — **new enforcement**, since CI doesn't run prettier today. Open decision, see below.
- `language: system` using the repo's own `node_modules` (`npx eslint`, `npx prettier --check`) to match CI's `npm ci` install exactly.
- **[CONCERN — project-reviewer, 2026-07-24] Implicit `npm ci` prerequisite.** `.prettierrc` declares `"plugins": ["prettier-plugin-tailwindcss"]`; if `frontend/node_modules` is absent (fresh checkout, or a backend-only contributor), the hook fails with a `Cannot find module` error rather than a formatting diagnostic. Note in the Phase 2 `CONTRIBUTING.md` section that the frontend hooks require `npm ci` in `frontend/` first.
- **[CONCERN — project-reviewer, 2026-07-24] Scope asymmetry, noted as a deliberate choice, not an oversight.** Prettier is scoped to `frontend/src/` (matching `npm run format`'s existing scope), but `eslint .` in CI reaches root-level frontend files too (`eslint.config.js`, `vite.config.ts`). If those files drift from `.prettierrc` formatting, the hook won't catch it. Keeping the narrower scope is fine — it matches the existing manual workflow exactly — but it's a decision, not an inherited default.

### infra/

- `az bicep lint --file infra/main.bicep` + per-module — mirrors `infra-ci.yml:85-92`.
- `az bicep build --file infra/main.bicep` — mirrors `infra-ci.yml:134`.
- **Prerequisite gap**: requires Azure CLI + bicep extension installed locally. Not every contributor works on `infra/`. Recommend this hook be **opt-in** (a separate `prek` stage, e.g. `manual` stage, or documented as "run `prek run infra-bicep-lint` before touching `infra/`") rather than default-on for every commit — a contributor without `az` installed shouldn't be blocked from committing unrelated backend/frontend work.
- **[CONCERN — project-reviewer, 2026-07-24] The per-module lint loop has no pre-commit-shaped implementation named yet.** CI's `for f in infra/modules/*.bicep; do az bicep lint --file "$f"; done` (`infra-ci.yml:88-92`) doesn't map cleanly onto pre-commit's file-passing model (`pass_filenames: true` passes staged files as args to one invocation; `false` passes none — neither loops per-file the way CI does). Since this hook is opt-in for Phase 3, it's not a Phase 1 blocker, but the Phase 3 implementer needs one concrete mechanism named before writing the config — either `pass_filenames: false` with `entry: bash -c 'for f in infra/modules/*.bicep; do az bicep lint --file "$f" || exit 1; done'`, or a thin wrapper script under `scripts/`. Decide at Phase 3 kickoff, not left open.

### General / cross-cutting (from the standard `pre-commit-hooks` repo)

| Hook | Recommend | Why |
|---|---|---|
| `trailing-whitespace` | Yes, default-on | Cheap, universal, zero false positives |
| `end-of-file-fixer` | Yes, default-on | Same |
| `check-yaml` | Yes, default-on | Catches malformed `.github/workflows/*.yml` before push |
| `check-added-large-files` | Yes, default-on | Repo has Playwright/image-gen assets — guard against accidental large binary commits |
| `check-merge-conflict` | Yes, default-on | Cheap safety net |
| `detect-private-key` | Yes, default-on | Repo handles `SESSION_SECRET`, `BOT_API_KEY`, Discord tokens — cheap secret-scan floor. Note: this repo's global `CLAUDE.md` already forbids the agent from grepping credential-shaped values directly (`# Credentials and Secrets`); this hook operates independently as a local pre-commit safety net, not a credential-inspection action taken by an agent. |
| commit-msg / conventional-commits lint | **Out of scope** for this plan (per #507 acceptance criteria) | no existing convention enforced today, would need a separate decision |

### Windows / CRLF caveat

Repo convention is `core.autocrlf=true`, working tree CRLF, repo stores LF (`~/.claude/CLAUDE.md § Line Endings (Windows)`, `.gitattributes`). `trailing-whitespace` and `end-of-file-fixer` hooks that rewrite line endings can produce "ghost modifications" — content identical, only line-ending noise — if hook output disagrees with `core.autocrlf` normalization. Recommend testing these two hooks specifically on a Windows checkout before enabling default-on, and configure them to not fight `.gitattributes` (most `pre-commit-hooks` mirrors respect the repo's line-ending config natively, but verify rather than assume). **Tracked as follow-up:** [#512](https://github.com/glitchwerks/rsl-siege-manager/issues/512) — verify trailing-whitespace/end-of-file-fixer don't fight CRLF on `.ps1` files specifically.
- **[NIT — project-reviewer, 2026-07-24] Risk is narrower than stated above.** `.gitattributes` already declares `eol=lf` for `.py`, `.ts`, `.tsx`, `.yml`, `.yaml`, `.json`, `.md`, `.txt` — those arrive as LF on a Windows checkout even without hook intervention, so the residual gap is file types `.gitattributes` doesn't cover (chiefly `.bicep`), which happens to line up with the already-opt-in infra/ area. Precise framing: the CRLF caveat matters for infra/ hooks specifically, not backend/frontend/bot/ Phase 1-2 hooks.

## Open decisions

1. **Prettier enforcement.** Two options:
   - (a) Enforce via `prek` now (`prettier --check`) — first place it becomes enforced anywhere, ahead of CI.
   - (b) Leave manual (`npm run format`), track CI enforcement as a separate follow-up if desired.
   - **Recommendation: (a)**, scoped to `frontend/src/` (matching `npm run format`'s existing scope, `frontend/package.json:10`) — low risk since `--check` only fails, never rewrites, and prettier config already exists (`frontend/.prettierrc`).

2. **Config placement.** Single root-level `.pre-commit-config.yaml` with `files:`/`exclude:` scoping per hook (as drafted above), not one config per area. `prek`/`pre-commit` config is inherently single-file at repo root; per-area scoping is achieved via each hook's `files:` regex, not via separate files. This avoids a 4-way config-sync burden and is the standard pattern for monorepos.

3. **infra/ hook default state.** Opt-in (manual stage or documented pre-push habit), not default-on — see prerequisite gap above.

## Phased rollout

0. **✅ Phase 0 — shipped (PR #509):** Add `black --check app/ tests/` + `ruff check app/ tests/` to the `bot-ci` job in `.github/workflows/ci.yml` (mirrors the existing `backend-ci` steps). Closes the CI gap so the Phase 1 bot/ hook mirrors a real gate instead of being the only enforcement surface for that area.
1. **✅ Phase 1 — shipped (PR #509):** Add `.pre-commit-config.yaml` with backend + bot black/ruff hooks and the general cross-cutting hooks (trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, check-merge-conflict, detect-private-key). Document in `CONTRIBUTING.md`, as a hard precondition (not a footnote): `prek install` is opt-in, but running it **requires the relevant area's venv active** — `language: system` hooks resolve whatever binary is first on `$PATH`, not a specific venv.
2. **Phase 2 (not started):** Add frontend eslint + prettier hooks once Phase 1 is validated on a few real commits (catches any Windows CRLF friction early, per caveat above, before adding more hook surface). Document that `npm ci` in `frontend/` must have been run first (the prettier hook depends on `prettier-plugin-tailwindcss` from `node_modules`).
3. **Phase 3:** Add infra bicep-lint/build hooks as an opt-in/manual stage, documented for infra contributors specifically. Before writing the config, decide the per-module lint mechanism (`pass_filenames: false` + inline loop, or a `scripts/` wrapper) — don't leave it open at implementation time.
4. **Phase 4 (optional, needs separate discussion):** Consider `prek install --install-hooks` as a documented onboarding step in `README.md` setup instructions once Phases 1-3 are stable. CI remains the authoritative gate throughout — no CI step is removed or replaced at any phase.

## Review trail

Reviewed by `project-reviewer` (2026-07-24) — 1 BLOCKING, 5 CONCERN, 3 NIT, all folded into the sections above with inline `[SEVERITY — project-reviewer, 2026-07-24]` tags. User declined an escalation to an adversarial `inquisitor`/`codex-reviewer` pass on top. Reviewer's overall assessment: the single root-level `.pre-commit-config.yaml` with per-hook `files:` scoping is architecturally sound for this monorepo (standard pre-commit pattern, no duplicated tool config); the highest-priority fix before Phase 1 ships is closing the `bot-ci` lint gap (Phase 0 above), since that's what makes every other area's "mirror, don't replace" framing actually true.

**2026-07-26:** Phase 0 + Phase 1 shipped via [PR #509](https://github.com/glitchwerks/rsl-siege-manager/pull/509), closing [#508](https://github.com/glitchwerks/rsl-siege-manager/issues/508) — the BLOCKING and Phase-0-shipped CONCERN above are resolved. This plan doc itself was committed after the fact via [PR #514](https://github.com/glitchwerks/rsl-siege-manager/pull/514), reviewed by CodeRabbit, closing [#507](https://github.com/glitchwerks/rsl-siege-manager/issues/507).

## Explicitly out of scope (per #507)

- Actually adding `.pre-commit-config.yaml` (this plan's Phase 1 is the follow-up issue, not this document)
- Replacing any existing CI lint/test step
- CI-side `prek run --all-files` integration
- Commit-message format enforcement
