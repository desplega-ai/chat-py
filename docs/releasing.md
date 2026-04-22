# Releasing chat-py

This is the release runbook for the 15-package `chat-py` workspace. It's the canonical procedure for cutting a new PyPI release; **follow it in order** unless you have a specific reason not to.

## Packages we publish

14 of the 15 workspace packages go to PyPI. `chat-py-integration-tests` stays workspace-only (it pulls in every adapter and is used for internal cross-adapter testing).

| Publish | Package name | Import name |
| --- | --- | --- |
| ✅ | `chat-py` | `chat` |
| ✅ | `chat-py-adapter-shared` | `chat_adapter_shared` |
| ✅ | `chat-py-adapter-slack` | `chat_adapter_slack` |
| ✅ | `chat-py-adapter-teams` | `chat_adapter_teams` |
| ✅ | `chat-py-adapter-gchat` | `chat_adapter_gchat` |
| ✅ | `chat-py-adapter-discord` | `chat_adapter_discord` |
| ✅ | `chat-py-adapter-github` | `chat_adapter_github` |
| ✅ | `chat-py-adapter-linear` | `chat_adapter_linear` |
| ✅ | `chat-py-adapter-telegram` | `chat_adapter_telegram` |
| ✅ | `chat-py-adapter-whatsapp` | `chat_adapter_whatsapp` |
| ✅ | `chat-py-adapter-state-memory` | `chat_adapter_state_memory` |
| ✅ | `chat-py-adapter-state-redis` | `chat_adapter_state_redis` |
| ✅ | `chat-py-adapter-state-ioredis` | `chat_adapter_state_ioredis` |
| ✅ | `chat-py-adapter-state-pg` | `chat_adapter_state_pg` |
| ❌ | `chat-py-integration-tests` | (workspace-only) |

## One-time setup

### Accounts

1. [PyPI account](https://pypi.org/account/register/) + 2FA enabled. Suggest an `@desplega.ai` org account.
2. [TestPyPI account](https://test.pypi.org/account/register/) + 2FA enabled. Separate account from PyPI.

### API tokens

For each of PyPI and TestPyPI, in **Account Settings → API tokens → Add API token**:

1. Token name: `chat-py-upload` (or similar).
2. Scope: **Entire account** the first time you upload a new package name; after the first upload you can rotate to per-project scope.
3. Copy the `pypi-…` string immediately — you can't view it again.

Export them locally; do NOT commit them:

```bash
# ~/.zshenv or similar
export TESTPYPI_TOKEN='pypi-AgENdGVzdC5weXBp...'
export PYPI_TOKEN='pypi-AgEIcHlwaS5vcmc...'
```

For CI, store them as `TESTPYPI_API_TOKEN` / `PYPI_API_TOKEN` GitHub Actions secrets.

## Pre-flight checklist

Run through these before every release:

- [ ] Working tree clean: `git status` is empty.
- [ ] On a release branch off latest `main`.
- [ ] `CHANGELOG.md` has a dated section for the new version; `[Unreleased]` is empty.
- [ ] Every `packages/*/pyproject.toml` carries the right `version = "X.Y.Z"` — use the same number across all 14 packages.
- [ ] Upstream parity reference bumped if applicable (`CHANGELOG.md` "tracks upstream `chat@X.Y.Z`" line).
- [ ] Full smoke green:

```bash
uv sync --all-packages --dev
uv run pytest packages/
uv run ruff check packages/
uv run ruff format --check packages/
uv run mypy packages/chat/src   # 32 errors pre-v0.2 is acceptable per parity notes
```

If any of the above drift, fix + commit before continuing.

## Build

```bash
rm -rf dist
uv build --all-packages
ls dist/                        # expect 30 artifacts — 15 sdists + 15 wheels
```

Remove `chat-py-integration-tests` artifacts so they never touch a public index:

```bash
rm dist/chat_py_integration_tests-*
ls dist/ | wc -l                # expect 28
```

## Dry run against TestPyPI

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token "$TESTPYPI_TOKEN"
```

Verify the install path end-to-end in a clean venv:

```bash
python -m venv /tmp/chatpy-testpypi-verify
source /tmp/chatpy-testpypi-verify/bin/activate
pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  chat-py chat-py-adapter-slack
python -c 'import chat, chat_adapter_slack; print("ok")'
deactivate
rm -rf /tmp/chatpy-testpypi-verify
```

`--extra-index-url` is required because TestPyPI does not host most third-party dependencies (httpx, slack-sdk, …).

If anything fails, fix forward with a new patch version — **TestPyPI releases can't be yanked/deleted** the same way PyPI ones can't be re-uploaded.

## Publish to PyPI

Once TestPyPI + the smoke install both pass:

```bash
uv publish --token "$PYPI_TOKEN"
```

Verify from PyPI:

```bash
python -m venv /tmp/chatpy-pypi-verify
source /tmp/chatpy-pypi-verify/bin/activate
pip install chat-py chat-py-adapter-slack
python -c 'import chat, chat_adapter_slack; print("ok")'
deactivate
rm -rf /tmp/chatpy-pypi-verify
```

## Tag and release

```bash
git tag v0.1.0 -m "chat-py 0.1.0 — initial port from vercel/chat"
git push origin v0.1.0
gh release create v0.1.0 --title "chat-py 0.1.0" --notes-file CHANGELOG.md
```

If `CHANGELOG.md` is too long for the release body, hand-craft a `/tmp/release-notes.md` (summary + install snippet + link back to the full CHANGELOG) and pass it via `--notes-file`.

## Post-publish

- Update the top-level `README.md` install snippet (`pip install chat-py-adapter-slack`, etc.) if it still references the pre-publish state.
- Open the next-cycle tracking issue for any Phase-3 E2E findings or adapter gaps.
- Announce (Slack / X / launch thread) — link the PyPI page and the gh release.

## Rollback

PyPI is write-once per version:

- You **cannot** re-upload the same `name == version`. Bump to the next patch and re-publish.
- You **can** yank a release (`pypi release yank <name> <version>` via the web UI): the version stays downloadable for existing pins but disappears from resolvers picking "latest".
- **Never delete** a version that's been public more than a few minutes — you'll break downstream pins and earn a lasting reputation problem.

If a release is catastrophic, yank all 14 packages simultaneously, bump to `X.Y.(Z+1)` with a `### Fixed` note citing the yanked release, and re-publish.

## CI publish (future)

Not wired for v0.1.0 — manual publish is fine. For v0.2.0+, add a workflow that triggers on `v*` tag push and runs `uv publish` with the `PYPI_API_TOKEN` secret. Guard with a smoke-test job that must pass first.
