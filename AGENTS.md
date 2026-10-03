# AGENTS.md

SlidgeMAX is an unofficial Slidge legacy module that bridges 1:1 MAX chats to XMPP through [PyMax](https://docs.pymax.org/). PyMax talks to an unofficial MAX API. There is no live MAX account in tests or CI.

User-facing docs (`README.md`, `CONTRIBUTING.md`) are Russian. Code, docstrings, and this file are English. Do not translate one into the other unless asked.

## Commands

```bash
uv sync --dev
uv run pytest
```

`pyproject.toml` sets `pythonpath = ["src"]` and `asyncio_mode = auto`. There is no lint or typecheck script. Do not invent one.

Dev run (no systemd):

```bash
uv run slidge --legacy-module slidgemax --jid max.example.org --secret "shared-secret" --home-dir ./data --server 127.0.0.1 --port 5347
```

The package entry point is `slidgemax` (`src/slidgemax/__main__.py`), which calls `slidge.entrypoint("slidgemax")`. Slidge is a library in this venv, not a separate daemon.

## Layout

| Path | Role |
| --- | --- |
| `src/slidgemax/gateway.py` | Component identity, SMS/2FA registration, search |
| `src/slidgemax/session.py` | One PyMax `Client` per XMPP user; event handlers |
| `src/slidgemax/contact.py` | `Contact` and `Roster` |
| `src/slidgemax/util.py` | Pure helpers. No Slidge imports |
| `src/slidgemax/config.py` | Plugin flags |
| `src/slidgemax/auth.py` | Queue-backed SMS and 2FA providers |
| `tests/` | Unit tests, no network |

`Gateway.session_cls` is `Session`. `Session.roster_cls` is `Roster`. `Roster.contact_cls` is `Contact`. `Gateway.GROUPS` is `False`. Contact JIDs are numeric MAX user ids: `123456@gateway`.

## Config

Upper-case names in `config.py` become CLI flags (`--device-type`), INI keys (`device-type`), and env vars (`SLIDGE_SLIDGEMAX_DEVICE_TYPE`). Pair each value with `NAME__DOC`.

Slidge inverts boolean flags whose default is true. `--presence` disables `PRESENCE`. Do not add a second `--no-*` flag.

## PyMax

Confirm APIs in the installed package (`.venv/.../pymax`) before using them. Do not guess method names or event fields.

- Handlers are `async def handler(event, client)`.
- `client.on_presence()` yields `PresenceEvent` (`user_id`, `presence`). `Presence.status` is `int | None`. `Presence.seen` is Unix time. The only verified status is `1` = online.
- `client.set_presence(online=)` is synchronous and only sets `app.config.interactive`. Login and `Session.on_presence` call it after the client is ready. Log and continue on failure. Do not send opcode 1 PING from this repo; `_ping_loop` publishes the flag. Status text is not bridged. It does not fetch other users' presence.
- Login models have no presence list. Do not parse raw login JSON. Forcing `presence_sync=-1` does not recover a snapshot.
- Last seen at login is opcode 35 `CONTACT_PRESENCE`: request `{"contactIds": [...]}`, response `{"presence": {"<id>": {"seen": <unix>, "status": <int>}}}`. PyMax 2.4.1 does not wrap this. `Session.refresh_presence` calls `client._app.invoke`. Apply the result only after `is_friend` is true; Slidge drops presence stanzas for non-friends.
- An existing 1:1 dialog is an XMPP friend, including bots. `Roster.fill` sets `is_friend` for address-book contacts and dialog peers. `Session._contact` sets it on the first live message, edit, or call, then calls `add_to_roster`. Search hits and bare JIDs stay non-friends until the user subscribes or a dialog event arrives. `on_friend_request` skips `add_contact` when `client_type` is `bot`. `on_friend_delete` calls `remove_contact` only if the id is in `max_contacts()`.
- 1:1 chat ids are the XOR of the two user ids (`dialog_chat_id` / `dialog_peer_id`).
- If PyMax does not expose the data, change PyMax, not this repo. See `CONTRIBUTING.md`.

## Style

- `from __future__ import annotations`. Match surrounding code. No comments unless asked.
- New modules under `src/slidgemax/` need the Apache-2.0 header (`.licenserc.yaml`, copyright `@black-roland`). Tests do not.
- Event handlers must catch their own errors and return. A handler exception must not kill the PyMax client.
- Tests construct PyMax models or call pure helpers. No sockets, no SMS, no real login.
- `pyproject.toml` `version` and `src/slidgemax/__init__.py` `__version__` can disagree. Do not sync them unless asked.

Do not commit unless the user asks.
