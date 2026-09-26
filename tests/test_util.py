from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pymax.protocol.enums import Opcode
from pymax.types.domain.attachments.call import CallAttachment
from pymax.types.domain.attachments.enums import CallType, HangupType
from pymax.types.domain.chat import Chat
from pymax.types.domain.enums import ChatType
from pymax.types.domain.message import Message
from pymax.types.domain.name import Name
from pymax.types.domain.profile import Profile
from pymax.types.domain.user import User

from slidgemax.util import (
    avatar_https_url,
    avatar_legacy_id,
    dialog_chat_id,
    dialog_peer_from_chat,
    dialog_peer_id,
    contact_profile_debug,
    display_name,
    max_client_type,
    is_call_attachment,
    is_group_chat,
    is_group_message,
    is_text_attachment_only,
    looks_like_incoming_call,
    message_timestamp,
    normalize_phone,
    parse_call_info,
    safe_filename,
    user_id,
    vcard_note,
)


def test_normalize_phone_e164() -> None:
    assert normalize_phone("+79990000000") == "+79990000000"
    assert normalize_phone("89990000000") == "+79990000000"
    assert normalize_phone("79990000000") == "+79990000000"
    assert normalize_phone("00 7 999 000-00-00") == "+79990000000"
    assert normalize_phone("+1 (202) 555-0100") == "+12025550100"


def test_normalize_phone_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_phone("")
    with pytest.raises(ValueError):
        normalize_phone("abc")
    with pytest.raises(ValueError):
        normalize_phone("+12")


def test_safe_filename() -> None:
    assert "at" in safe_filename("User@Example.ORG/res")
    assert "@" not in safe_filename("a@b")


def test_dialog_xor() -> None:
    me, peer = 10, 3
    chat_id = dialog_chat_id(me, peer)
    assert chat_id == me ^ peer
    assert dialog_peer_id(chat_id, me) == peer


def test_user_id_from_profile_and_user() -> None:
    user = User(id=42, names=[Name(name="Ada")])
    assert user_id(user) == 42
    assert user_id(Profile(contact=user)) == 42
    assert user_id(42) == 42
    assert user_id(None) is None


def test_display_name() -> None:
    user = User(id=7, names=[Name(first_name="Ada", last_name="Lovelace")])
    assert display_name(user) == "Ada Lovelace"
    assert display_name(User(id=7, names=[Name(name="Ada")])) == "Ada"
    assert display_name(None, fallback="x") == "x"
    assert display_name(User(id=9, names=[])) == "MAX 9"


def test_max_client_type() -> None:
    alice = User(
        id=6738397,
        options=["BOT", "HAS_WEBAPP"],
        web_app="https://alice.example/onboarding",
        gender=1,
    )
    master = User(id=452, options=["BOT", "RESTRICTED"], web_app=None, gender=1)
    assert max_client_type(alice) == "bot"
    assert max_client_type(master) == "bot"
    assert max_client_type(User(id=1, options=["TT", "ONEME"])) == "phone"
    assert max_client_type(User(id=2, options=["OFFICIAL", "ONEME"])) == "phone"
    assert max_client_type(User(id=3, options=["HAS_WEBAPP"], web_app="https://x")) == "phone"
    assert max_client_type(User(id=4)) == "phone"
    assert max_client_type(User.model_construct(id=5, options=None)) == "phone"
    assert max_client_type(User.model_construct(id=6, options="BOT")) == "phone"
    assert max_client_type(User.model_construct(id=7, options="bot")) == "phone"


def test_contact_profile_debug_keeps_bot_shape_and_omits_phone() -> None:
    user = User.model_validate(
        {
            "id": 6738397,
            "names": [{"name": "Alice", "type": "NICK"}],
            "phone": 79990000000,
            "baseUrl": "https://i.oneme.ru/i?r=secret",
            "baseRawUrl": "https://i.oneme.ru/i?r=raw",
            "gender": 1,
            "webApp": "https://alice.example/onboarding",
            "options": ["BOT"],
            "mystery": {"kind": "bot"},
        }
    )
    data = contact_profile_debug(user)
    assert "phone" not in data
    assert "base_url" not in data
    assert "base_raw_url" not in data
    assert data["gender"] == 1
    assert data["web_app"] == "https://alice.example/onboarding"
    assert data["options"] == ["BOT"]
    assert data["mystery"] == {"kind": "bot"}
    assert data["__types__"]["gender"] == "int"
    assert data["__types__"]["web_app"] == "str"
    assert "gender" in data["__fields_set__"]
    assert "web_app" in data["__fields_set__"]
    assert "phone" not in data["__fields_set__"]


def test_vcard_note() -> None:
    cases = (
        None,
        12,
        "",
        "   ",
        "  bio  ",
        "line\n  two",
        "hel\x00lo",
        " \x00 ",
    )
    notes = [vcard_note(value) for value in cases]
    assert notes == [None, None, None, None, "bio", "line\n  two", "hello", None]
    assert all(note is None or "MAX id" not in note for note in notes)


def test_avatar_https_url() -> None:
    assert avatar_https_url(None) is None
    assert avatar_https_url(12) is None
    assert avatar_https_url("   ") is None
    assert avatar_https_url("http://i.oneme.ru/i?r=x") is None
    assert avatar_https_url("  https://i.oneme.ru/i?r=x  ") == "https://i.oneme.ru/i?r=x"


def test_avatar_legacy_id() -> None:
    assert avatar_legacy_id(None) is None
    assert avatar_legacy_id(True) is None
    assert avatar_legacy_id(0) is None
    assert avatar_legacy_id(-3) is None
    assert avatar_legacy_id(42) == "42"


def _chat(**kwargs: object) -> Chat:
    defaults = dict(
        id=1,
        type=ChatType.DIALOG,
        status="ACTIVE",
        owner=2,
        last_event_time=0,
        created=0,
    )
    defaults.update(kwargs)
    return Chat(**defaults)  # type: ignore[arg-type]


def test_is_group_chat() -> None:
    assert not is_group_chat(_chat(type=ChatType.DIALOG))
    assert is_group_chat(_chat(type=ChatType.CHAT))
    assert is_group_chat(_chat(type=ChatType.CHANNEL))


def test_dialog_peer_from_chat() -> None:
    chat = _chat(id=10 ^ 3, type=ChatType.DIALOG, owner=3, participants={10: 0, 3: 0})
    assert dialog_peer_from_chat(chat, 10) == 3
    assert dialog_peer_from_chat(_chat(type=ChatType.CHAT, owner=3), 10) is None


def test_is_group_message() -> None:
    msg = Message(id=1, time=1, type="DIALOG", sender=3, chat_id=1, text="hi")
    assert not is_group_message(msg)
    assert is_group_message(Message(id=1, time=1, type="CHANNEL", text="x"))


def test_looks_like_incoming_call() -> None:
    assert looks_like_incoming_call(Opcode.NOTIF_CALL_START, {})
    assert looks_like_incoming_call(137, {})
    assert looks_like_incoming_call(0, {"incomingCall": {"callerId": 1}})
    assert not looks_like_incoming_call(1, {})


def test_parse_call_attachment() -> None:
    attach = CallAttachment(
        type="CALL",  # type: ignore[arg-type]
        contact_ids=[99],
        call_type=CallType.VIDEO,
        hangup_type=HangupType.MISSED,
    )
    info = parse_call_info(attach)
    assert info["caller_id"] == 99
    assert info["video"] is True
    assert info["hangup"] == "MISSED"
    assert is_call_attachment(attach)


def test_parse_call_payload() -> None:
    info = parse_call_info({"callerId": 5, "isVideo": True, "name": "Ada"})
    assert info["caller_id"] == 5
    assert info["video"] is True
    assert info["name"] == "Ada"


def test_text_only_attachments() -> None:
    msg = Message(id=1, time=1, type="DIALOG", text="hi", attaches=[])
    assert is_text_attachment_only(msg)


def test_message_timestamp_ms() -> None:
    msg = Message(id=1, time=1_700_000_000_000, type="DIALOG", text="hi")
    ts = message_timestamp(msg)
    assert isinstance(ts, datetime)
    assert ts.tzinfo == UTC
    assert ts.year == 2023
