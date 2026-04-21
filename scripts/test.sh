#!/usr/bin/env bash
set -euo pipefail
uv run pytest packages/ "$@"
