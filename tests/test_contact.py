from __future__ import annotations

from types import SimpleNamespace

from pymax.types.domain.user import User

from slidgemax.contact import Contact
from slidgemax.session import Session


class _Session:
    def __init__(self, contacts: list[User] | None = None) -> None:
        self.added: list[int] = []
        self.removed: list[int] = []
        self.refreshed: list[list[int]] = []
        self._contacts = contacts or []

    async def add_max_contact(self, ident: int) -> None:
        self.added.append(ident)

    async def remove_max_contact(self, ident: int) -> None:
        self.removed.append(ident)

    async def refresh_presence(self, ids: list[int]) -> None:
        self.refreshed.append(list(ids))

    def max_contacts(self) -> list[User]:
        return self._contacts


def _contact(
    *,
    legacy_id: str = "20",
    client_type: str = "phone",
    is_friend: bool = False,
    contacts: list[User] | None = None,
) -> tuple[SimpleNamespace, _Session, list[str]]:
    session = _Session(contacts)
    calls: list[str] = []
    contact = SimpleNamespace(
        legacy_id=legacy_id,
        client_type=client_type,
        is_friend=is_friend,
        session=session,
    )

    def apply_presence() -> None:
        calls.append(f"presence:{contact.is_friend}")

    async def accept_friend_request() -> None:
        calls.append("accept")

    async def add_to_roster() -> None:
        calls.append(f"roster:{contact.is_friend}")

    contact.apply_presence = apply_presence
    contact.accept_friend_request = accept_friend_request
    contact.add_to_roster = add_to_roster
    return contact, session, calls


async def test_friend_request_skips_add_for_bot() -> None:
    contact, session, calls = _contact(client_type="bot")

    await Contact.on_friend_request(contact)  # type: ignore[arg-type]

    assert session.added == []
    assert contact.is_friend is True
    assert session.refreshed == [[20]]
    assert calls == ["presence:True", "accept"]


async def test_friend_request_adds_phone_contact() -> None:
    contact, session, calls = _contact()

    await Contact.on_friend_request(contact)  # type: ignore[arg-type]

    assert session.added == [20]
    assert contact.is_friend is True
    assert calls == ["presence:True", "accept"]


async def test_friend_delete_removes_address_book_contact_only() -> None:
    listed, listed_session, _calls = _contact(contacts=[User(id=20)])
    dialog, dialog_session, _dialog_calls = _contact()
    listed.is_friend = True
    dialog.is_friend = True

    await Contact.on_friend_delete(listed)  # type: ignore[arg-type]
    await Contact.on_friend_delete(dialog)  # type: ignore[arg-type]

    assert listed_session.removed == [20]
    assert dialog_session.removed == []
    assert listed.is_friend is False
    assert dialog.is_friend is False


async def test_mark_dialog_friend_rosters_once() -> None:
    contact, _session, calls = _contact()

    await Contact.mark_dialog_friend(contact)  # type: ignore[arg-type]
    await Contact.mark_dialog_friend(contact)  # type: ignore[arg-type]

    assert contact.is_friend is True
    assert calls == ["presence:True", "roster:True"]


async def test_contact_lookup_marks_dialog_friend() -> None:
    marked: list[str] = []

    class _Contacts:
        async def by_legacy_id(self, legacy_id: str) -> SimpleNamespace:
            contact = SimpleNamespace(legacy_id=legacy_id)

            async def mark_dialog_friend() -> None:
                marked.append(legacy_id)

            contact.mark_dialog_friend = mark_dialog_friend
            return contact

    session = Session.__new__(Session)
    session.contacts = _Contacts()  # type: ignore[assignment]

    contact = await session._contact(20)

    assert contact.legacy_id == "20"
    assert marked == ["20"]
