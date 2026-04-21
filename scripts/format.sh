#!/usr/bin/env bash
set -euo pipefail
uv run ruff format packages/
uv run ruff check --fix packages/
