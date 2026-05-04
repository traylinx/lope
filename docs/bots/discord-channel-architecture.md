# Discord Channel Architecture — Phase 1 Design Doc

**Status:** Phase 1 Research & Architecture  
**Author:** Harvey (Lope Sprint Agent)  
**Date:** 2026-04-30  
**Based on:** `ChannelPlugin` interface (`lib-harvey-core/src/core/chat/channels/base.py`), `HarveyChat` gateway (`lib-harvey-core/src/core/chat/gateway.py`), multi-channel refactor plan

---

## 1. Overview

This doc maps the Discord channel implementation to the existing `ChannelPlugin` interface and the `HarveyChat` gateway. It defines which methods are required, how the `on_message` callback wires in, and what Discord-specific capabilities to expose.

**Guiding principle:** Discord should be a drop-in `ChannelPlugin` alongside Telegram, Slack, and WhatsApp. No gateway surgery.

---

## 2. Discord-Specific Constraints

Unlike Telegram (long-polling via python-telegram-bot) and Slack (Socket Mode), Discord requires:

| Constraint | Implication |
|---|---|
| **WebSocket-based gateway** | Discord uses `discord.py` / `nextcord` with `@bot.event async def on_message(...)`. Cannot use the shared `_polling.py` backoff utilities directly. |
| **No message edit/delete** | `EDIT_MESSAGE` / `DELETE_MESSAGE` capabilities = 0. Unlike Slack which has `chat.update`. |
| **Typing indicators** via `channel.typing` context manager | Fire-and-forget 5s pulse. No `send_typing(active=False)` support. |
| **Slash commands vs text** | Discord原生 uses slash commands. HarveyChat uses natural text. Both work via `on_message` — slash commands also emit message events. |
| **Guild / DM duality** | `on_message` fires for both guild channels and DMs. DM = `channel.type == discord.ChannelType.private`. |
| **Thread support** | Messages in threads have `message.thread` set. Replies should respect thread context. |

---

## 3. ChannelPlugin Interface → Discord Mapping

### 3.1 Required Abstract Methods

| `ChannelPlugin` method | Discord implementation | Notes |
|---|---|---|
| `name` → `"discord"` | `return "discord"` | Registry key |
| `start(on_message)` | Initialize `nextcord.Intents.messages`, register `on_message` handler, start bot | See §4 |
| `stop()` | Call `bot.close()` or `await bot.logout()` | Graceful shutdown |
| `send_text(target, text, reply_to=..., parse_mode=...)` | `channel = bot.get_channel(int(target))` then `channel.send(text)` | `target` = channel ID string |
| `capabilities` | See §5 | Bitmask |
| `is_configured()` | Check `DISCORD_BOT_TOKEN` env/config | |

### 3.2 Optional Override Methods

| Method | Discord support | Implementation note |
|---|---|---|
| `send_typing(target, active=True)` | **Yes** — via `async with channel.typing:` | `nextcord` context manager fires 5s pulse. No explicit stop. |
| `edit_text(...)` | **No** | Not supported by Discord API for bot messages beyond 15min window |
| `delete_message(...)` | **Partial** | `message.delete()` works but not channel-level bulk delete |
| `send_document(target, path, ...)` | **Yes** | `channel.send(file=discord.File(path))` |
| `send_photo(target, path, ...)` | **Yes** | Same as document; Discord treats images as file with preview |

### 3.3 Gateway Call Flow

```
Discord message arrives
    │
    ▼
on_message(event)   ← registered in start(on_message)
    │
    ▼
_normalize(discord.Message) → (channel="discord", user_id=str(msg.author.id),
                                username=str(msg.author.name), text=msg.content)
    │
    ▼
gateway.handle_message(channel, user_id, username, text)
    │
    ▼
gateway._send_file() → for file_type=="file": await ch.send_document(...)
                       for file_type=="photo": await ch.send_photo(...)
```

---

## 4. on_message Callback Wiring

### 4.1 Signature Compatibility

`ChannelPlugin.start()` receives:
```python
async def on_message(channel: str, user_id: str, username: str, text: str) -> str:
    """
    Returns: response text to send back to the user
    """
```

### 4.2 Discord Event Handler

```python
class DiscordChannel(ChannelPlugin):
    async def start(self, on_message: Callable[..., Awaitable[Any]]) -> None:
        self._on_message = on_message
        intents = nextcord.Intents.default()
        intents.message_content = True  # Required for text content
        self._bot = nextcord.Client(intents=intents)

        @self._bot.event
        async def on_message(msg: nextcord.Message):
            # Ignore bot messages (including self)
            if msg.author.bot:
                return
            # Ignore empty messages
            if not msg.content:
                return

            # Determine reply target (thread vs channel)
            target = str(msg.channel.id)
            if msg.thread:
                target = str(msg.thread.id)  # Reply into thread

            try:
                response = await self._on_message(
                    channel="discord",
                    user_id=str(msg.author.id),
                    username=msg.author.name,
                    text=msg.content,
                )
                if response:
                    await self._send_text(target, response)
            except Exception as e:
                log.error(f"[discord] handler error: {e}", exc_info=True)

        await self._bot.start(self.config.bot_token)
```

### 4.3 DM Handling

```python
async def on_message(msg: nextcord.Message):
    # DM: channel.type == nextcord.ChannelType.private
    if isinstance(msg.channel, nextcord.DMChannel):
        target = str(msg.channel.id)  # DM channel ID is stable
    else:
        target = str(msg.channel.id)
```

---

## 5. Discord Capability Profile

```python
from core.chat.channels.capabilities import ChannelCapability

@property
def capabilities(self) -> ChannelCapability:
    return (
        ChannelCapability.TYPING_INDICATOR   # via context manager
        | ChannelCapability.SEND_DOCUMENT    # File()
        | ChannelCapability.SEND_PHOTO       # File() with image preview
        | ChannelCapability.SEND_VIDEO       # File()
        | ChannelCapability.REPLY_TO_MESSAGE # using message.reference
        | ChannelCapability.FORWARD_MESSAGE  # partial (can't forward to arbitrary channel)
        | ChannelCapability.PIN_MESSAGE       # message.pin()
    )
```

**Excluded:** `EDIT_MESSAGE`, `DELETE_MESSAGE`, `INLINE_BUTTONS` (use components instead), `STREAMING`, `MEDIA_GROUP`.

---

## 6. Config Schema

```python
# core/chat/channels/discord/config.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class DiscordConfig:
    bot_token: str = ""
    allowed_guild_ids: list[int] = field(default_factory=list)  # whitelist guilds
    allowed_user_ids: list[int] = field(default_factory=list)   # whitelist DMs (optional)
    default_nickname: str = "Harvey"
    reply_to_threads: bool = True

    def is_configured(self) -> bool:
        return bool(self.bot_token)
```

**Config loading priority:**  
1. `DiscordConfig` instance passed to constructor  
2. `config.channels.discord` dict from `ChatConfig`  
3. Env vars: `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_GUILDS`, `DISCORD_ALLOWED_USERS`

---

## 7. Package Structure

```
core/chat/channels/discord/
├── __init__.py           # ChannelRegistry.register("discord", DiscordChannel)
├── channel.py            # DiscordChannel(ChannelPlugin)
├── config.py            # DiscordConfig dataclass
├── send.py              # _send_text, _send_document, _send_photo, _send_typing
├── inbound.py           # _normalize_message, _extract_attachments
├── errors.py             # DiscordError, classify_error(), ErrorCategory mapping
└── utils.py             # HTML→Discord markdown, mention parsing, etc.
```

**Decision:** Split into sub-modules (send/inbound/errors) unlike the current Telegram monolith. This mirrors the Phase 2 target structure from the multi-channel refactor plan and makes Phase 2 Discord migration clean.

---

## 8. File → Channel Method Mapping (Gateway → Channel)

| Gateway method | Channel method called | Discord equivalent |
|---|---|---|
| `gateway.handle_message(...)` | `_on_message(...)` | `on_message` event |
| `gateway._send_file(channel, user_id, "file", path)` | `ch.send_document(target, path)` | `channel.send(file=File(path))` |
| `gateway._send_file(channel, user_id, "photo", path)` | `ch.send_photo(target, path)` | `channel.send(file=File(path))` |
| `gateway._send_thinking_feedback()` | `ch.send_typing(target)` | `async with channel.typing:` |
| `gateway._get_status()` | — | Static reply, no channel call |
| `gateway._handle_workflow()` | `ch.send_text(target, msg)` | `channel.send(msg)` |

---

## 9. Error Classification

```python
# core/chat/channels/discord/errors.py
class DiscordErrorCooldown(ChannelErrorCooldown):
    """Per-channel error suppression for Discord."""

    # ErrorCategory mapping:
    # nextcord.Forbidden (403) + "Cannot send messages" → BOT_BLOCKED
    # nextcord.HTTPException 429 → RATE_LIMITED
    # ConnectionError / OSError ENOTFOUND → PRE_CONNECT
    # nextcord.HTTPException 500/502/503 → RECOVERABLE
```

Rate limits are stricter on Discord (1 req/sec global for most endpoints). The cooldowns module's `ChannelErrorCooldown` should be wired in for `BOT_BLOCKED` after 3 consecutive 403s.

---

## 10. Open Questions for Phase 2

1. **Slash commands vs text:** Discord natively uses `/` commands. Should we register application commands for common intents (`/harvey`, `/search`, etc.) or rely on natural text in all messages?

2. **Guild role requirements:** If `allowed_guild_ids` is empty (no restriction), should DMs be the only entry point? Or allow any guild the bot is added to?

3. **Thread vs channel reply:** When a user replies in a thread, `target` should be the thread ID. Does the store need a `thread_id` column, or is `channel_id` sufficient for deduplication?

4. **Attachment handling:** Discord messages can have multiple attachments. Should we:
   - Transcribe text files and inline them?
   - Upload to S3/Cloudflare R2 and send as links?
   - Forward raw file to the bridge for processing?

5. **Embed vs plain text:** Discord supports rich embeds. Should Harvey responses use embeds for structured data (search results, code blocks with syntax highlighting)?

---

## 11. Phase 2 Deliverables Checklist

- [ ] `core/chat/channels/discord/config.py` — `DiscordConfig`
- [ ] `core/chat/channels/discord/__init__.py` — registers with `ChannelRegistry`
- [ ] `core/chat/channels/discord/channel.py` — `DiscordChannel(ChannelPlugin)`
- [ ] `core/chat/channels/discord/send.py` — outbound methods
- [ ] `core/chat/channels/discord/inbound.py` — message normalization
- [ ] `core/chat/channels/discord/errors.py` — error classification
- [ ] `core/chat/channels/discord/utils.py` — markdown conversion, etc.
- [ ] Update `core/chat/config.py` to load `channels.discord` dict
- [ ] Update `core/chat/gateway.py` to include `DiscordChannel` when configured
- [ ] Unit tests: `test_discord_channel.py`, `test_capabilities.py`
- [ ] Integration test: real Discord guild + DM flow

---

## 12. References

- `lib-harvey-core/src/core/chat/channels/base.py` — `ChannelPlugin` ABC
- `lib-harvey-core/src/core/chat/channels/capabilities.py` — `ChannelCapability` flags
- `lib-harvey-core/src/core/chat/channels/registry.py` — `ChannelRegistry.create()`
- `lib-harvey-core/src/core/chat/gateway.py` — `HarveyChat.handle_message()`, `_send_file()`, `_bridge_send_with_file_hints()`
- `lib-harvey-core/src/core/chat/channels/slack/channel.py` — stub reference implementation
- `lib-harvey-core/src/core/chat/channels/telegram/channel.py` — live reference implementation
- `development/sprints/SPRINT-HARVEYCHAT-ROBUSTNESS/multi-channel-refactor-plan.md` — full architecture rationale
