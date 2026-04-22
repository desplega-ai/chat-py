"""Shared helpers for the manual E2E scripts under `examples/e2e/<adapter>/`.

Every scenario script uses these helpers to:

- Load `.env` from the repo root (so you don't re-paste tokens every run).
- Validate that the env vars it declares are actually set; exit fast with a
  clear, actionable message if any are missing.
- Mount a `chat.handle_webhook` call behind a FastAPI route with the right
  content-type handling.
- Run the server on a configurable port via uvicorn.

Keep this module small on purpose — it's infrastructure glue, not business
logic. The interesting stuff lives in the per-scenario scripts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chat import Chat


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load `<repo-root>/.env` if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.exit("[e2e] python-dotenv not installed. Run `uv sync --group e2e` first.")
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        sys.exit(
            f"[e2e] {env_file} not found. Create a .env at the repo root with the "
            "env vars listed in this script's docstring."
        )
    load_dotenv(env_file)


def require_env(*names: str) -> dict[str, str]:
    """Return a dict of the requested env vars, or exit with a helpful message.

    Prefer this over os.environ[...] directly so a missing token is a clean
    failure instead of a KeyError 40 lines deep in adapter code.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        val = os.environ.get(name)
        if not val:
            missing.append(name)
        else:
            values[name] = val
    if missing:
        sys.exit(
            "[e2e] missing required env vars: " + ", ".join(missing) + "\n"
            "      add them to .env at the repo root, or export them in your shell."
        )
    return values


def run_webhook_server(
    bot: Chat,
    adapter_name: str,
    *,
    port: int = 8000,
    route: str | None = None,
    extra_routes: Iterable[tuple[str, Any]] = (),
) -> None:
    """Start a FastAPI server that forwards inbound webhooks to `bot`.

    - `adapter_name` is the key you registered the adapter under on `Chat(...)`.
    - `route` defaults to `/api/webhooks/<adapter_name>`.
    - `extra_routes` lets a scenario mount additional handlers (e.g. health
      checks, static URL-verify endpoints).
    """
    try:
        import uvicorn
        from fastapi import FastAPI, Request
    except ImportError:
        sys.exit("[e2e] fastapi / uvicorn not installed. Run `uv sync --group e2e` first.")

    app = FastAPI()
    webhook_route = route or f"/api/webhooks/{adapter_name}"

    @app.post(webhook_route)
    async def handle(request: Request) -> Any:  # type: ignore[no-redef]
        body = await request.body()
        headers = dict(request.headers)
        status, resp_headers, resp_body = await bot.handle_webhook(adapter_name, body, headers)
        from fastapi.responses import Response

        return Response(content=resp_body, status_code=status, headers=dict(resp_headers))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "adapter": adapter_name}

    for path, handler in extra_routes:
        app.add_api_route(path, handler, methods=["GET", "POST"])

    print(f"[e2e] mounted POST {webhook_route}")
    print("[e2e] health check  GET /health")
    print(f"[e2e] listening on  http://127.0.0.1:{port}")
    print(
        f"[e2e] next step: `ngrok http {port}` and paste the https URL into the "
        "provider's webhook config."
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
