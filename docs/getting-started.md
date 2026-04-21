# Getting started

A runnable walkthrough is the best way in. See:

- [`examples/fastapi-chat`](../examples/fastapi-chat) — Slack bot on FastAPI
- [`examples/telegram-chat`](../examples/telegram-chat) — Telegram bot

For a full API reference, see [`docs/api/`](api/).

## Minimal skeleton

```python
from chat import Chat
from chat_adapter_slack import create_slack_adapter
from chat_adapter_state_memory import create_memory_state

bot = Chat(
    user_name="mybot",
    adapters={"slack": create_slack_adapter()},
    state=create_memory_state(),
)

@bot.on_new_mention
async def handle(thread, message):
    await thread.post(f"Hi! You said: {message.text}")
```

Wire it to any ASGI framework via `bot.handle_webhook(name, body, headers)`.

