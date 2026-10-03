from __future__ import annotations

from types import SimpleNamespace

from pymax.types.domain.user import User

from slidgemax.contact import Roster


def _user(ident: int) -> User:
    return User(id=ident)


class _Chat:
    def __init__(self, peer: int | None) -> None:
        self.peer = peer


class _Client:
    def __init__(
        self,
        users: list[User] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._users = users or []
        self._error = error
        self.get_users_calls: list[list[int]] = []
        self.get_user_calls: list[int] = []

    async def get_users(self, user_ids: list[int]) -> list[User]:
        self.get_users_calls.append(list(user_ids))
        if self._error is not None:
            raise self._error
        wanted = set(user_ids)
        return [user for user in self._users if user.id in wanted]

    async def get_user(self, user_id: int) -> User | None:
        self.get_user_calls.append(user_id)
        return None


class _Session:
    def __init__(
        self,
        *,
        contacts: list[User] | None = None,
        chats: list[_Chat] | None = None,
        client: _Client | None = None,
        me_id: int | None = 1,
    ) -> None:
        self._contacts = contacts or []
        self._chats = chats or []
        self.client = client
        self.me_id = me_id
        self.presence_ids: list[int] = []

    async def refresh_presence(self, user_ids: list[int]) -> None:
        self.presence_ids = list(user_ids)

    def max_contacts(self) -> list[User]:
        return self._contacts

    def max_chats(self) -> list[_Chat]:
        return self._chats

    def peer_from_chat(self, chat: _Chat) -> int | None:
        return chat.peer


def _roster(
    session: _Session,
    *,
    existing: set[str] | None = None,
) -> tuple[Roster, list[tuple[object, ...]]]:
    roster = Roster.__new__(Roster)
    roster.session = session  # type: ignore[assignment]
    calls: list[tuple[object, ...]] = []
    known = existing or set()

    async def by_legacy_id(legacy_id: str, *args: object) -> SimpleNamespace:
        calls.append((legacy_id, *args))
        contact = SimpleNamespace(is_friend=False, legacy_id=legacy_id, applied=False)

        def apply_presence() -> None:
            contact.applied = True

        contact.apply_presence = apply_presence
        return contact

    def by_legacy_id_if_exists(legacy_id: str) -> SimpleNamespace | None:
        if legacy_id in known:
            return SimpleNamespace(legacy_id=legacy_id)
        return None

    roster.by_legacy_id = by_legacy_id  # type: ignore[method-assign]
    roster.by_legacy_id_if_exists = by_legacy_id_if_exists  # type: ignore[method-assign]
    return roster, calls


async def _fill(roster: Roster) -> list[SimpleNamespace]:
    return [contact async for contact in roster.fill()]


async def test_fill_batches_dialog_peers_missing_from_address_book() -> None:
    friend = _user(10)
    peer_a = _user(20)
    peer_b = _user(30)
    client = _Client([peer_a, peer_b])
    session = _Session(
        contacts=[friend],
        chats=[_Chat(20), _Chat(30)],
        client=client,
    )
    roster, calls = _roster(session)

    yielded = await _fill(roster)

    assert client.get_users_calls == [[20, 30]]
    assert client.get_user_calls == []
    assert calls == [
        ("10", friend),
        ("20", peer_a, True),
        ("30", peer_b, True),
    ]
    assert [item.legacy_id for item in yielded] == ["10", "20", "30"]
    assert all(item.is_friend for item in yielded)
    assert session.presence_ids == [10, 20, 30]
    assert all(item.applied for item in yielded)


async def test_fill_does_not_retry_omitted_peer() -> None:
    peer_a = _user(20)
    client = _Client([peer_a])
    session = _Session(chats=[_Chat(20), _Chat(30)], client=client)
    roster, calls = _roster(session)

    await _fill(roster)

    assert client.get_users_calls == [[20, 30]]
    assert client.get_user_calls == []
    assert calls == [("20", peer_a, True), ("30", None, True)]


async def test_fill_does_not_retry_when_get_users_fails() -> None:
    client = _Client(error=RuntimeError("down"))
    session = _Session(chats=[_Chat(20)], client=client)
    roster, calls = _roster(session)

    await _fill(roster)

    assert client.get_users_calls == [[20]]
    assert client.get_user_calls == []
    assert calls == [("20", None, True)]


async def test_fill_without_client_marks_dialog_peers_resolved() -> None:
    session = _Session(chats=[_Chat(20)], client=None)
    roster, calls = _roster(session)

    await _fill(roster)

    assert calls == [("20", None, True)]


async def test_fill_skips_fetch_for_updated_dialog_peer() -> None:
    client = _Client([_user(20)])
    session = _Session(chats=[_Chat(20)], client=client)
    roster, calls = _roster(session, existing={"20"})

    yielded = await _fill(roster)

    assert client.get_users_calls == []
    assert calls == [("20",)]
    assert yielded[0].is_friend is True
    assert yielded[0].applied is True


async def test_fill_skips_self_group_and_address_book_dialog() -> None:
    friend = _user(10)
    peer = _user(20)
    client = _Client([peer])
    session = _Session(
        contacts=[friend, friend],
        chats=[
            _Chat(1),
            _Chat(None),
            _Chat(10),
            _Chat(20),
            _Chat(20),
        ],
        client=client,
        me_id=1,
    )
    roster, calls = _roster(session)

    yielded = await _fill(roster)

    assert client.get_users_calls == [[20]]
    assert calls == [("10", friend), ("20", peer, True)]
    assert [item.legacy_id for item in yielded] == ["10", "20"]
    assert all(item.is_friend for item in yielded)
    assert all(item.applied for item in yielded)
