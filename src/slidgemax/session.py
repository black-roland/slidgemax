# Copyright 2026 @black-roland
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Per-user MAX session: PyMax client lifecycle and event mapping."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymax import Client, ExtraConfig, PresenceEvent, SyncOverrides
from pymax.api.session.enums import DeviceType
from pymax.protocol.enums import Opcode
from pymax.protocol.models import InboundFrame
from pymax.types.domain.attachments.call import CallAttachment
from pymax.types.domain.chat import Chat
from pymax.types.domain.message import Message
from pymax.types.domain.presence import Presence
from pymax.types.domain.user import User
from slidge import BaseSession, global_config
from slidge.command import FormField, SearchResult
from slidge.group import LegacyBookmarks
from slidge.util.types import PseudoPresenceShow
from slixmpp.exceptions import XMPPError
from slixmpp.types import ResourceDict

from . import config
from .auth import QueuePasswordProvider, QueueSmsProvider
from .client import (
    REVOKED_LOGIN_MESSAGE,
    LoginTokenRevoked,
    MaxClient,
    RefuseSmsAuth,
)
from .contact import Roster
from .util import (
    contact_presence_entries,
    dialog_chat_id,
    dialog_peer_from_chat,
    dialog_peer_id,
    display_name,
    is_call_attachment,
    is_group_chat,
    is_group_message,
    is_text_attachment_only,
    looks_like_incoming_call,
    map_presence,
    message_timestamp,
    xmpp_show_online,
    normalize_phone,
    parse_call_info,
    payload_as_dict,
    safe_filename,
    sender_id,
    user_id,
)

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger(__name__)

_PRESENCE_CHUNK = 50


def session_dir(jid: str) -> Path:
    return Path(global_config.HOME_DIR) / "max_sessions" / safe_filename(jid)


def max_extra_config() -> ExtraConfig:
    try:
        device_type = DeviceType[config.DEVICE_TYPE.upper()]
    except KeyError as exc:
        raise ValueError(
            f"Invalid DEVICE_TYPE {config.DEVICE_TYPE!r}; use DESKTOP, ANDROID or IOS"
        ) from exc
    return ExtraConfig(
        reconnect=config.RECONNECT,
        reconnect_delay=config.RECONNECT_DELAY,
        device_type=device_type,
        host=config.MAX_HOST,
        port=int(config.MAX_PORT),
        use_ssl=config.MAX_USE_SSL,
        log_level="INFO",
        persist_session=True,
        relogin=False,
        telemetry=False,
        sync=SyncOverrides(contacts_sync=-1),
    )


def make_client(
    phone: str,
    work_dir: Path,
    *,
    sms_provider: QueueSmsProvider | None = None,
    password_provider: QueuePasswordProvider | None = None,
) -> Client:
    work_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "phone": phone,
        "work_dir": str(work_dir),
        "session_name": "session.db",
        "extra_config": max_extra_config(),
    }
    if sms_provider is not None:
        kwargs["sms_code_provider"] = sms_provider
    else:
        kwargs["auth_flow"] = RefuseSmsAuth()
    if password_provider is not None:
        kwargs["password_provider"] = password_provider
    return MaxClient(**kwargs)


class Session(BaseSession[Roster, LegacyBookmarks]):
    """One PyMax client bound to one registered XMPP user."""

    xmpp: Gateway

    def __init__(self, user) -> None:  # noqa: ANN001
        super().__init__(user)
        self.client: Client | None = None
        self._me_id: int | None = None
        self._bound = False
        self._presence: dict[int, Presence] = {}
        self._unknown_presence: set[int] = set()
        self._max_online: bool | None = None

    @property
    def phone(self) -> str:
        return str(self.user.get("phone") or "")

    @property
    def me_id(self) -> int | None:
        if self._me_id is not None:
            return self._me_id
        if self.client is not None:
            ident = user_id(self.client.me)
            if ident is not None:
                self._me_id = ident
                return ident
        stored = self.user.get("max_user_id")
        if stored not in (None, ""):
            try:
                self._me_id = int(stored)  # type: ignore[arg-type]
                return self._me_id
            except (TypeError, ValueError):
                return None
        return None

    def work_dir(self) -> Path:
        return session_dir(self.user.jid.bare)

    async def login(self) -> str:
        pending = self.xmpp.pending.pop(self.user.jid.bare, None)
        if pending is not None and pending.client is not None:
            if pending.failed.is_set() and not pending.ready.is_set():
                raise RuntimeError(pending.error or "MAX login failed")
            if not pending.ready.is_set():
                state = await pending.wait(config.REGISTRATION_TIMEOUT)
                if state != "ready":
                    raise RuntimeError(
                        pending.error or f"MAX login did not finish (state={state})"
                    )
            self.client = pending.client
            self._me_id = pending.me_id or user_id(self.client.me)
            self._bind_client(self.client)
        else:
            phone = self.phone
            if not phone:
                raise RuntimeError("No MAX phone number stored for this account")
            work_dir = self.work_dir()
            self.client = make_client(phone, work_dir)
            self._bind_client(self.client)
            self.create_task(self._run_client(), name=f"pymax:{self.user.jid.bare}")
            # _bind_client wires on_start to set logged-in state via an Event we wait on.
            await self._wait_client_ready()

        self._publish_online(True)

        ident = self.me_id
        if ident is not None:
            self.contacts.user_legacy_id = str(ident)
            self.legacy_module_data_update(
                {
                    "max_user_id": ident,
                    "max_name": display_name(self.client.me if self.client else None),
                }
            )
        name = str(self.user.get("max_name") or display_name(self.client.me if self.client else None))
        return f"Connected as {name} (MAX {ident or '?'})"

    async def _wait_client_ready(self) -> None:
        ready = getattr(self, "_client_ready")
        failed = getattr(self, "_client_failed")
        waiters = [
            asyncio.create_task(ready.wait()),
            asyncio.create_task(failed.wait()),
        ]
        done, pending = await asyncio.wait(
            waiters, timeout=config.REGISTRATION_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if failed.is_set() and not ready.is_set():
            raise RuntimeError(getattr(self, "_client_error", None) or "MAX login failed")
        if not ready.is_set():
            raise TimeoutError("MAX login timed out")

    def _bind_client(self, client: Client) -> None:
        if self._bound:
            return
        self._bound = True
        self._client_ready = asyncio.Event()
        self._client_failed = asyncio.Event()
        self._client_error: str | None = None

        @client.on_start()
        async def on_start(c: Client) -> None:
            self._me_id = user_id(c.me)
            self._client_ready.set()
            self.log.info("MAX session ready (id=%s)", self._me_id)

        if config.PRESENCE:

            @client.on_presence()
            async def on_presence(event: PresenceEvent, _c: Client) -> None:
                try:
                    self._on_max_presence(event)
                except Exception:
                    self.log.exception("Error handling MAX presence")

        @client.on_message()
        async def on_message(message: Message, _c: Client) -> None:
            try:
                await self._on_max_message(message)
            except Exception:
                self.log.exception("Error handling incoming MAX message")

        @client.on_message_edit()
        async def on_edit(message: Message, _c: Client) -> None:
            try:
                await self._on_max_edit(message)
            except Exception:
                self.log.exception("Error handling MAX message edit")

        @client.on_raw()
        async def on_raw(frame: InboundFrame, _c: Client) -> None:
            try:
                await self._on_max_raw(frame)
            except Exception:
                self.log.exception("Error handling MAX raw frame")

        @client.on_error()
        async def on_error(*args: Any) -> None:
            exc = next((a for a in args if isinstance(a, BaseException)), None)
            self._client_error = str(exc) if exc else "MAX client error"
            self.log.warning("MAX client error: %s", self._client_error)
            if not self._client_ready.is_set():
                self._client_failed.set()

    def notify_login_revoked(self) -> None:
        self.send_gateway_message(REVOKED_LOGIN_MESSAGE)
        self.send_gateway_status(REVOKED_LOGIN_MESSAGE, show="dnd")

    async def _run_client(self) -> None:
        assert self.client is not None
        try:
            await self.client.start()
        except LoginTokenRevoked as exc:
            self._client_error = str(exc)
            self.log.error("%s", exc)
            self._client_failed.set()
            if self._client_ready.is_set():
                self.notify_login_revoked()
        except Exception as exc:
            self._client_error = str(exc) or type(exc).__name__
            self.log.exception("MAX client crashed: %s", exc)
            self._client_failed.set()

    async def logout(self) -> None:
        client = self.client
        self.client = None
        if client is not None:
            try:
                await client.stop()
            except Exception:
                self.log.debug("client.stop() failed", exc_info=True)

    async def on_unregister(self) -> None:
        await self.logout()
        shutil.rmtree(self.work_dir(), ignore_errors=True)

    def max_contacts(self) -> list[User]:
        if self.client is None:
            return []
        return [c for c in (self.client.contacts or []) if c is not None]

    def max_chats(self) -> list[Chat]:
        if self.client is None:
            return []
        return list(self.client.chats or [])

    def _find_chat(self, chat_id: int) -> Chat | None:
        for chat in self.max_chats():
            if chat.id == chat_id:
                return chat
        return None

    def peer_from_chat(self, chat: Chat) -> int | None:
        if config.IGNORE_GROUPS and is_group_chat(chat):
            return None
        return dialog_peer_from_chat(chat, self.me_id)

    def dialog_chat_id(self, peer_id: int) -> int:
        me = self.me_id
        if me is None:
            raise RuntimeError("MAX account id is unknown")
        if self.client is not None:
            return self.client.get_chat_id(me, peer_id)
        return dialog_chat_id(me, peer_id)

    def resolve_dialog_peer(self, message: Message) -> int | None:
        me = self.me_id
        if config.IGNORE_GROUPS and is_group_message(message):
            return None
        chat_id = message.chat_id
        sid = sender_id(message)
        if chat_id is not None:
            chat = self._find_chat(chat_id)
            if chat is not None:
                if config.IGNORE_GROUPS and is_group_chat(chat):
                    return None
                peer = dialog_peer_from_chat(chat, me)
                if peer is not None:
                    return peer
            if me is not None:
                peer = dialog_peer_id(chat_id, me)
                if sid is None:
                    return None if config.IGNORE_GROUPS else peer
                if sid in {me, peer}:
                    return peer
                if config.IGNORE_GROUPS:
                    return None
        if sid is not None and sid != me:
            return sid
        return None

    async def send_text(self, peer_id: int, text: str) -> Message:
        if self.client is None:
            raise XMPPError("recipient-unavailable", "MAX session is not connected")
        chat_id = self.dialog_chat_id(peer_id)
        return await self.client.send_message(chat_id=chat_id, text=text)

    async def edit_text(self, peer_id: int, message_id: int, text: str) -> Message:
        if self.client is None:
            raise XMPPError("recipient-unavailable", "MAX session is not connected")
        chat_id = self.dialog_chat_id(peer_id)
        return await self.client.edit_message(
            chat_id=chat_id, message_id=message_id, text=text
        )

    async def delete_text(self, peer_id: int, message_id: int) -> None:
        if self.client is None:
            raise XMPPError("recipient-unavailable", "MAX session is not connected")
        chat_id = self.dialog_chat_id(peer_id)
        await self.client.delete_message(
            chat_id=chat_id, message_ids=[message_id], for_me=False
        )

    async def add_max_contact(self, user_id_: int) -> None:
        if self.client is None:
            return
        try:
            await self.client.add_contact(user_id_)
        except Exception:
            self.log.debug("add_contact(%s) failed", user_id_, exc_info=True)

    async def remove_max_contact(self, user_id_: int) -> None:
        if self.client is None:
            return
        try:
            await self.client.remove_contact(user_id_)
        except Exception:
            self.log.debug("remove_contact(%s) failed", user_id_, exc_info=True)

    async def on_search(self, form_values: dict[str, str]) -> SearchResult | None:
        query = (form_values.get("query") or form_values.get("phone") or "").strip()
        if not query:
            return None
        if self.client is None:
            raise XMPPError("recipient-unavailable", "MAX session is not connected")

        user: User | None = None
        if query.isdigit():
            user = await self.client.get_user(int(query))
        else:
            try:
                phone = normalize_phone(query)
            except ValueError as exc:
                raise XMPPError("bad-request", str(exc)) from exc
            try:
                user = await self.client.search_by_phone(phone)
            except Exception as exc:
                self.log.debug("search_by_phone failed", exc_info=True)
                raise XMPPError("item-not-found", f"No MAX user for {phone}") from exc

        if user is None:
            return None
        ident = user_id(user)
        if ident is None:
            return None
        jid = f"{ident}@{self.xmpp.boundjid.bare}"
        return SearchResult(
            fields=[
                FormField(var="jid", label="JID"),
                FormField(var="name", label="Name"),
                FormField(var="id", label="MAX id"),
            ],
            items=[
                {
                    "jid": jid,
                    "name": display_name(user, fallback=f"MAX {ident}"),
                    "id": str(ident),
                }
            ],
        )

    def _publish_online(self, online: bool) -> None:
        if not config.PRESENCE or self.client is None or online == self._max_online:
            return
        try:
            self.client.set_presence(online=online)
        except Exception:
            self.log.warning("set_presence(online=%s) failed", online, exc_info=True)
            return
        self._max_online = online

    async def on_presence(
        self,
        resource: str,
        show: PseudoPresenceShow,
        status: str,
        resources: dict[str, ResourceDict],
        merged_resource: ResourceDict | None,
    ) -> None:
        online = merged_resource is not None and xmpp_show_online(
            str(merged_resource.get("show") or "")
        )
        self._publish_online(online)

    def cached_presence(self, user_id_: int) -> Presence | None:
        return self._presence.get(user_id_)

    def _remember_presence(self, uid: int, presence: Presence) -> None:
        old = self._presence.get(uid)
        status = presence.status
        seen = presence.seen
        if old is not None and old.status == 1 and status != 1:
            status = 1
        if seen is None and old is not None:
            seen = old.seen
        if map_presence(status, seen) is None:
            return
        self._presence[uid] = Presence(status=status, seen=seen)

    async def refresh_presence(self, user_ids: list[int]) -> None:
        if not config.PRESENCE or self.client is None:
            return
        me = self.me_id
        unique: list[int] = []
        seen: set[int] = set()
        for uid in user_ids:
            if not isinstance(uid, int) or uid <= 0 or uid == me or uid in seen:
                continue
            seen.add(uid)
            unique.append(uid)
        if not unique:
            return
        for start in range(0, len(unique), _PRESENCE_CHUNK):
            chunk = unique[start : start + _PRESENCE_CHUNK]
            try:
                response = await self.client._app.invoke(
                    Opcode.CONTACT_PRESENCE,
                    {"contactIds": chunk},
                )
            except Exception:
                self.log.warning(
                    "CONTACT_PRESENCE failed for %s contacts", len(chunk), exc_info=True
                )
                continue
            for uid, status, seen_at in contact_presence_entries(response.payload):
                if uid not in seen:
                    continue
                self._remember_presence(uid, Presence(status=status, seen=seen_at))
        self.log.info(
            "MAX presence snapshot: %s/%s contacts",
            sum(1 for uid in unique if uid in self._presence),
            len(unique),
        )

    def _on_max_presence(self, event: PresenceEvent) -> None:
        if not config.PRESENCE:
            return
        uid = event.user_id
        if not isinstance(uid, int) or uid <= 0 or uid == self.me_id:
            return
        presence = event.presence
        status = presence.status
        if (
            isinstance(status, int)
            and status != 1
            and status not in self._unknown_presence
        ):
            self._unknown_presence.add(status)
            self.log.info("Unknown MAX presence status %s for user %s", status, uid)
        self.log.info(
            "MAX presence user=%s status=%s seen=%s", uid, status, presence.seen
        )
        self._remember_presence(uid, presence)
        if uid not in self._presence:
            return
        contact = self.contacts.by_legacy_id_if_exists(str(uid))
        if contact is not None:
            contact.apply_presence(self._presence[uid])

    async def _contact(self, peer_id: int):
        contact = await self.contacts.by_legacy_id(str(peer_id))
        await contact.mark_dialog_friend()
        return contact

    async def _on_max_message(self, message: Message) -> None:
        peer = self.resolve_dialog_peer(message)
        if peer is None:
            return
        contact = await self._contact(peer)

        carbon = self.me_id is not None and sender_id(message) == self.me_id
        when = message_timestamp(message)
        legacy_id = str(message.id)

        for attach in message.attaches or []:
            if is_call_attachment(attach):
                await self._notify_call(contact, attach, when=when, carbon=carbon)
                return

        body = (message.text or "").strip()
        if not body:
            if config.PLACEHOLDER_UNSUPPORTED and not is_text_attachment_only(message):
                body = config.UNSUPPORTED_TEXT
            else:
                return

        contact.send_text(
            body,
            legacy_msg_id=legacy_id,
            when=when,
            carbon=carbon,
        )

    async def _on_max_edit(self, message: Message) -> None:
        peer = self.resolve_dialog_peer(message)
        if peer is None:
            return
        contact = await self._contact(peer)
        carbon = self.me_id is not None and sender_id(message) == self.me_id
        contact.correct(
            str(message.id),
            message.text or "",
            when=message_timestamp(message),
            carbon=carbon,
        )

    async def _on_max_raw(self, frame: InboundFrame) -> None:
        if not config.CALL_NOTIFICATIONS:
            return
        payload = payload_as_dict(frame.payload)
        if not looks_like_incoming_call(frame.opcode, payload):
            return
        info = parse_call_info(payload)
        caller = info.get("caller_id")
        if caller is None and info.get("chat_id") is not None and self.me_id is not None:
            caller = dialog_peer_id(int(info["chat_id"]), self.me_id)
        if caller is None:
            return
        contact = await self._contact(int(caller))
        await self._notify_call(contact, info)

    async def _notify_call(
        self,
        contact,
        extra: CallAttachment | dict[str, Any],
        *,
        when: datetime | None = None,
        carbon: bool = False,
    ) -> None:
        if not config.CALL_NOTIFICATIONS:
            return
        info = parse_call_info(extra if isinstance(extra, dict) else extra)
        text = config.CALL_VIDEO_TEXT if info.get("video") else config.CALL_VOICE_TEXT
        hangup = (info.get("hangup") or "").upper()
        if hangup == "MISSED":
            text = f"Missed call — {text}"
        name = info.get("name")
        if name:
            text = f"{text} ({name})"
        contact.send_text(text, when=when, carbon=carbon)
