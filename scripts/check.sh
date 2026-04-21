#!/usr/bin/env bash
set -euo pipefail
uv run ruff check packages/
uv run ruff format --check packages/
