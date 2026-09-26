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

"""Shared helpers: phones, names, MAX ids, vCard notes, avatars, call/group detection, presence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pymax.protocol.enums import Opcode
from pymax.types.domain.attachments.call import CallAttachment
from pymax.types.domain.attachments.enums import AttachmentType, CallType, HangupType
from pymax.types.domain.chat import Chat
from pymax.types.domain.enums import ChatType
from pymax.types.domain.message import Message
from pymax.types.domain.profile import Profile
from pymax.types.domain.user import User

_PHONE_STRIP = re.compile(r"[\s\-().]")
_UNSAFE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]+")

INCOMING_CALL_OPCODE = int(Opcode.NOTIF_CALL_START)


def normalize_phone(raw: str) -> str:
    """Normalize a phone number to a +E.164-ish form MAX will accept."""
    s = _PHONE_STRIP.sub("", (raw or "").strip())
    if not s:
        raise ValueError("phone number is empty")
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("8") and len(s) == 11 and s[1:].isdigit():
        s = "+7" + s[1:]
    elif s.startswith("7") and len(s) == 11 and s.isdigit():
        s = "+" + s
    elif not s.startswith("+"):
        if not s.isdigit():
            raise ValueError(f"invalid phone number: {raw!r}")
        s = "+" + s
    if not s[1:].isdigit():
        raise ValueError(f"invalid phone number: {raw!r}")
    if len(s) < 8:
        raise ValueError(f"phone number too short: {raw!r}")
    return s


def safe_filename(jid: str) -> str:
    """Stable filesystem name for a bare JID."""
    bare = (jid or "").split("/", 1)[0].strip().lower()
    return _UNSAFE_FILENAME.sub("_", bare.replace("@", "_at_")) or "user"


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    ident = getattr(value, "id", None)
    if isinstance(ident, int):
        return ident
    contact = getattr(value, "contact", None)
    if contact is not None:
        cid = getattr(contact, "id", None)
        if isinstance(cid, int):
            return cid
    return None


def user_id(value: Any) -> int | None:
    """Extract a MAX user id from a User, Profile, or int-like value."""
    if isinstance(value, Profile):
        return int_or_none(value.contact)
    return int_or_none(value)


def sender_id(message: Message) -> int | None:
    return int_or_none(message.sender)


def dialog_chat_id(first_user_id: int, second_user_id: int) -> int:
    """MAX computes 1:1 chat IDs as XOR of the two user IDs."""
    return first_user_id ^ second_user_id


def dialog_peer_id(chat_id: int, me_id: int) -> int:
    return chat_id ^ me_id


def chat_type_name(chat: Any) -> str:
    if chat is None:
        return ""
    t = getattr(chat, "type", None)
    if t is None:
        return ""
    name = getattr(t, "name", None) or getattr(t, "value", None)
    if name:
        return str(name).upper()
    return str(t).upper()


def is_group_chat(chat: Any) -> bool:
    if isinstance(chat, Chat):
        t = chat.type
        if t == ChatType.DIALOG or t == ChatType.DIALOG.value:
            return False
        if t in {ChatType.CHAT, ChatType.CHANNEL} or t in {
            ChatType.CHAT.value,
            ChatType.CHANNEL.value,
        }:
            return True
    name = chat_type_name(chat)
    if not name:
        return False
    if "DIALOG" in name:
        return False
    return "GROUP" in name or name == "CHAT" or "CHANNEL" in name


def is_group_message(message: Message) -> bool:
    kind = (message.type or "").upper()
    return kind in {"CHAT", "CHANNEL", "GROUP"}


def dialog_peer_from_chat(chat: Chat, me_id: int | None) -> int | None:
    if is_group_chat(chat):
        return None
    if chat.participants:
        for uid in chat.participants:
            if me_id is None or uid != me_id:
                return int(uid)
    owner = int_or_none(chat.owner)
    if owner is not None and owner != me_id:
        return owner
    if me_id is not None:
        return dialog_peer_id(chat.id, me_id)
    return None


def display_name(user: Any, fallback: str | None = None) -> str:
    if user is None:
        return fallback or "MAX user"
    if isinstance(user, Profile):
        user = user.contact
    names = getattr(user, "names", None) or []
    if names:
        n = names[0]
        full = getattr(n, "name", None)
        if full:
            return str(full)
        parts = [getattr(n, "first_name", None), getattr(n, "last_name", None)]
        joined = " ".join(p for p in parts if p)
        if joined:
            return joined
    for attr in ("name", "first_name", "nick"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    ident = user_id(user)
    if ident is not None:
        return fallback or f"MAX {ident}"
    return fallback or "MAX user"


_PROFILE_DEBUG_OMIT = frozenset({"phone", "base_url", "base_raw_url"})
_PROFILE_DEBUG_TYPES = (
    "gender",
    "web_app",
    "link",
    "menu_button",
    "options",
    "account_status",
    "status",
)


def max_client_type(user: User) -> Literal["bot", "phone"]:
    """XMPP client type for a MAX profile. ``BOT`` in a list of options, else phone."""
    options = getattr(user, "options", None)
    if isinstance(options, list) and "BOT" in options:
        return "bot"
    return "phone"


def contact_profile_debug(user: User) -> dict[str, Any]:
    """Profile fields for debug logs. Omits phone and avatar URLs."""
    data = user.model_dump(exclude=set(_PROFILE_DEBUG_OMIT))
    data["__types__"] = {
        name: type(getattr(user, name)).__name__ for name in _PROFILE_DEBUG_TYPES
    }
    data["__fields_set__"] = sorted(user.model_fields_set - _PROFILE_DEBUG_OMIT)
    return data


def vcard_note(description: Any) -> str | None:
    """Return a contact vCard note from a MAX profile description.

    Non-strings, including None, are omitted. Ends are stripped and NUL bytes
    removed. An empty result is omitted. The note is never prefixed with a MAX id.
    """
    if not isinstance(description, str):
        return None
    text = description.strip().replace("\x00", "")
    return text or None


def avatar_https_url(base_url: Any) -> str | None:
    """Return a stripped https avatar URL, or None for anything else."""
    if not isinstance(base_url, str):
        return None
    text = base_url.strip()
    if not text.startswith("https://"):
        return None
    return text


def avatar_legacy_id(photo_id: Any) -> str | None:
    """Return a positive photo id as text. Booleans and other values are omitted."""
    if isinstance(photo_id, bool) or not isinstance(photo_id, int):
        return None
    if photo_id <= 0:
        return None
    return str(photo_id)


def payload_as_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump(by_alias=True)
        except TypeError:
            dumped = dump()
        if isinstance(dumped, dict):
            return dumped
    extra = getattr(payload, "model_extra", None)
    if isinstance(extra, dict):
        return extra
    return {}


def looks_like_incoming_call(opcode: Any, payload: dict[str, Any]) -> bool:
    value = opcode if isinstance(opcode, int) else getattr(opcode, "value", None)
    name = str(getattr(opcode, "name", opcode) or "").upper()
    if value == INCOMING_CALL_OPCODE or value == Opcode.NOTIF_CALL_START:
        return True
    if "INCOMING_CALL" in name or name in {"NOTIF_CALL_START", "NOTIF_INCOMING_CALL"}:
        return True
    if not payload:
        return False
    keys = {str(k).lower() for k in payload}
    if "incomingcall" in keys or (
        "isvideo" in keys and ("callerid" in keys or "initiatorid" in keys)
    ):
        return True
    nested = payload.get("call") or payload.get("incomingCall") or payload.get("incoming_call")
    return isinstance(nested, dict)


def parse_call_info(payload: dict[str, Any] | CallAttachment | None) -> dict[str, Any]:
    if isinstance(payload, CallAttachment):
        caller = payload.contact_ids[0] if payload.contact_ids else None
        video = payload.call_type == CallType.VIDEO
        return {
            "caller_id": int_or_none(caller),
            "chat_id": None,
            "video": bool(video),
            "name": None,
            "hangup": payload.hangup_type.value if payload.hangup_type else None,
        }
    data: dict[str, Any] = payload or {}
    nested = data.get("call") or data.get("incomingCall") or data.get("incoming_call")
    if isinstance(nested, dict):
        data = nested
    caller = (
        data.get("callerId")
        or data.get("caller_id")
        or data.get("initiatorId")
        or data.get("initiator_id")
        or data.get("userId")
        or data.get("user_id")
        or data.get("contactId")
        or data.get("contact_id")
    )
    chat_id = (
        data.get("chatId")
        or data.get("chat_id")
        or data.get("conversationId")
        or data.get("conversation_id")
    )
    video = data.get("isVideo")
    if video is None:
        video = data.get("video")
    if video is None:
        kind = str(data.get("type") or data.get("callType") or "").upper()
        video = "VIDEO" in kind
    hangup = data.get("hangupType") or data.get("hangup_type")
    return {
        "caller_id": int_or_none(caller),
        "chat_id": int_or_none(chat_id),
        "video": bool(video),
        "name": data.get("name") or data.get("callerName") or data.get("title"),
        "hangup": str(hangup) if hangup else None,
    }


def attachment_type_name(attach: Any) -> str:
    t = getattr(attach, "type", None)
    if t is not None:
        name = getattr(t, "name", None) or getattr(t, "value", None) or str(t)
        return str(name).upper()
    return type(attach).__name__.upper()


def is_call_attachment(attach: Any) -> bool:
    if isinstance(attach, CallAttachment):
        return True
    t = getattr(attach, "type", None)
    if t == AttachmentType.CALL or t == AttachmentType.CALL.value:
        return True
    return "CALL" in attachment_type_name(attach)


def is_text_attachment_only(message: Message) -> bool:
    attaches = list(message.attaches or [])
    if not attaches:
        return True
    return all(attachment_type_name(a) in {"", "CONTROL", "NONE"} for a in attaches)


_PRESENCE_MS = 1_000_000_000_000
PresenceShow = Literal["online", "away"]


def presence_seen(seen: int | None) -> datetime | None:
    """Convert a MAX presence timestamp to UTC, or None if it is not usable."""
    if seen is None or seen <= 0:
        return None
    seconds = seen / 1000 if seen >= _PRESENCE_MS else float(seen)
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def xmpp_show_online(show: str) -> bool:
    """Return whether an XMPP presence show should be published as MAX online.

    Only an empty show (available) and ``chat`` are online. ``away``, ``xa``,
    ``dnd``, and any other value are offline. Status text is not part of this.
    """
    return show in {"", "chat"}


def map_presence(
    status: int | None, seen: int | None
) -> tuple[PresenceShow, datetime | None] | None:
    """Map a MAX presence push to an XMPP show and last-seen stamp.

    Status ``1`` is online. A usable ``seen`` without that status, including an
    unknown status code, is away. Empty or non-positive fields are ignored so a
    previous status is not cleared.
    """
    last_seen = presence_seen(seen)
    if status == 1:
        return ("online", last_seen)
    if last_seen is None:
        return None
    return ("away", last_seen)


def _presence_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def contact_presence_entries(
    payload: object,
) -> list[tuple[int, int | None, int | None]]:
    """Parse an opcode 35 ``presence`` map into ``(user_id, status, seen)``.

    The map is keyed by user id. Login payloads are not a presence list and are
    ignored here.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("presence")
    if not isinstance(raw, dict):
        return []
    entries: list[tuple[int, int | None, int | None]] = []
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            uid = int(key)
        except (TypeError, ValueError):
            continue
        if uid <= 0:
            continue
        entries.append((uid, _presence_int(value.get("status")), _presence_int(value.get("seen"))))
    return entries


def message_timestamp(message: Message):
    """Return a timezone-aware datetime, or None if MAX sent nothing useful."""
    raw = message.time
    if not raw:
        return None
    # MAX sometimes sends milliseconds.
    seconds = raw / 1000 if raw > 10_000_000_000 else raw
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
