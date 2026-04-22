# Manual E2E scripts

These are intentionally **not** pytest tests. They're standalone Python scripts you run by hand against real provider APIs + a tunneled local webhook. Use them when you want to verify the full loop before cutting a release or after touching an adapter.

## Layout

```
examples/e2e/
├─ _common.py            shared env-loading + FastAPI webhook runner
├─ slack/
│  └─ echo.py            @mention → reply; subscribed thread → echo
├─ discord/
├─ gchat/
├─ teams/
├─ github/
├─ linear/
├─ telegram/
└─ whatsapp/
```

Each scenario is a single self-contained script. Read its module docstring for required env vars, provider setup steps, and run instructions.

## Prereqs (once)

```bash
uv sync --group e2e      # installs fastapi + uvicorn + python-dotenv
brew install ngrok       # or any tunnel of your choice
```

Then create a `.env` in the repo root with whatever tokens the scenario needs. Each script lists its env vars in the top docstring; a script will exit early with a clear message if any are missing.

## Running

General shape:

```bash
uv run python examples/e2e/<adapter>/<scenario>.py
```

e.g.

```bash
uv run python examples/e2e/slack/echo.py
```

In a second terminal:

```bash
ngrok http 8000
```

Paste the ngrok HTTPS URL + the mount path (shown by the script on start, e.g. `/api/webhooks/slack`) into the provider's webhook config. Trigger a message in the provider UI and watch the server logs.

Set `E2E_PORT=<port>` to run on a different port (default 8000).

## Writing a new scenario

1. Make a new file under `examples/e2e/<adapter>/<scenario>.py`.
2. Start with a docstring covering: what it tests, required env vars, provider setup, run command.
3. `load_env()` + `require_env(...)` from `_common` — never read `os.environ` directly, so missing env is a clean failure.
4. Build a `Chat(...)` with `create_memory_state()` (or whichever state backend the scenario wants to exercise).
5. Register handlers (`@bot.on_new_mention`, `@bot.on_subscribed_message`, …).
6. Hand it to `run_webhook_server(bot, "<adapter-key>", port=...)`.

No pytest wiring. No fixtures. Just a script you can iterate on by re-running.
