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

"""Legacy contacts and the per-user roster."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from slidge.contact import LegacyContact, LegacyRoster
from slidge.util.types import ContactMessage, HoleBound
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from . import config
from .avatar import SetAvatarMixin
from .util import (
    contact_profile_debug,
    display_name,
    map_presence,
    max_client_type,
    user_id,
    vcard_note,
)

if TYPE_CHECKING:
    from pymax.types.domain.presence import Presence
    from pymax.types.domain.user import User

    from .session import Session

log = logging.getLogger(__name__)


class Contact(SetAvatarMixin, LegacyContact):
    """A MAX user presented as an XMPP contact ``<id>@gateway``."""

    session: Session

    AVATAR = True
    RECEIPTS = False
    MARKS = False
    CHAT_STATES = False
    UPLOAD = False
    CORRECTION = True
    REACTION = False
    RETRACTION = True
    REPLIES = False

    async def update_info(self, user: User | None = None, resolved: bool = False) -> None:
        session = self.session
        ident = int(self.legacy_id)
        if user is None and not resolved and session.client is not None:
            try:
                user = await session.client.get_user(ident)
            except Exception:
                log.debug("get_user(%s) failed", ident, exc_info=True)
                user = None
        if user is not None:
            log.debug("MAX contact profile %s", contact_profile_debug(user))
            self.name = display_name(user, fallback=f"MAX {ident}")
            phone = getattr(user, "phone", None)
            self.set_vcard(
                full_name=self.name,
                phone=str(phone) if phone else None,
                note=vcard_note(user.description),
            )
            desired = max_client_type(user)
            if self.client_type != desired:
                self.client_type = desired
                self.update_stored_attribute(caps_ver=None)
        elif not self.name:
            self.name = f"MAX {ident}"
        self.apply_presence()
        if user is not None:
            await self.update_avatar(user.base_url, user.photo_id)

    async def backfill(self, after: HoleBound | None) -> None:
        return

    def apply_presence(self, presence: Presence | None = None) -> None:
        if not config.PRESENCE:
            return
        if presence is None:
            presence = self.session.cached_presence(int(self.legacy_id))
        if presence is None:
            return
        mapped = map_presence(presence.status, presence.seen)
        if mapped is None:
            return
        show, last_seen = mapped
        if show == "online":
            self.online(last_seen=last_seen)
        else:
            self.away(last_seen=last_seen)

    async def on_message(self, message: ContactMessage) -> str | None:
        if message.attachments:
            raise XMPPError(
                "feature-not-implemented",
                "This MAX gateway does not send files, stickers or other attachments.",
            )
        text = (message.body or "").strip()
        if not text:
            return None
        peer = int(self.legacy_id)
        if message.replace:
            await self.session.edit_text(peer, int(message.replace), text)
            return message.replace
        sent = await self.session.send_text(peer, text)
        return str(sent.id)

    async def on_sticker(self, sticker) -> str | None:  # noqa: ANN001
        raise XMPPError(
            "feature-not-implemented",
            "Stickers are not bridged by this MAX gateway.",
        )

    async def on_retract(self, legacy_msg_id: str, thread: str | None) -> None:
        await self.session.delete_text(int(self.legacy_id), int(legacy_msg_id))

    async def on_friend_request(self, text: str = "") -> None:
        ident = int(self.legacy_id)
        if self.client_type != "bot":
            await self.session.add_max_contact(ident)
        self.is_friend = True
        await self.session.refresh_presence([ident])
        self.apply_presence()
        await self.accept_friend_request()

    async def on_friend_delete(self, text: str = "") -> None:
        ident = int(self.legacy_id)
        if any(user_id(user) == ident for user in self.session.max_contacts()):
            await self.session.remove_max_contact(ident)
        self.is_friend = False

    async def on_friend_accept(self) -> None:
        self.is_friend = True

    async def mark_dialog_friend(self) -> None:
        if self.is_friend:
            return
        self.is_friend = True
        self.apply_presence()
        await self.add_to_roster()


class Roster(LegacyRoster[Contact]):
    session: Session

    async def jid_username_to_legacy_id(self, jid_username: str) -> str:
        if not jid_username.isdigit():
            raise XMPPError(
                "bad-request",
                "MAX contact JIDs use numeric user IDs (for example 123456@gateway).",
            )
        value = int(jid_username)
        if value <= 0:
            raise XMPPError("bad-request", "MAX user id must be a positive integer.")
        return str(value)

    def by_legacy_id_if_exists(self, legacy_id: str) -> Contact | None:
        return self.by_jid_only_if_exists(
            JID(f"{legacy_id}@{self.session.xmpp.boundjid.bare}")
        )

    async def fill(self) -> AsyncIterator[Contact]:
        session = self.session
        seen: set[str] = set()
        me = session.me_id

        ready: list[Contact] = []

        for user in session.max_contacts():
            ident = user_id(user)
            if ident is None or ident == me:
                continue
            key = str(ident)
            if key in seen:
                continue
            seen.add(key)
            contact = await self.by_legacy_id(key, user)
            contact.is_friend = True
            ready.append(contact)

        dialog_peers: list[int] = []
        for chat in session.max_chats():
            peer = session.peer_from_chat(chat)
            if peer is None or peer == me:
                continue
            key = str(peer)
            if key in seen:
                continue
            seen.add(key)
            dialog_peers.append(peer)

        pending = [
            peer
            for peer in dialog_peers
            if self.by_legacy_id_if_exists(str(peer)) is None
        ]
        fetched = await self._fetch_users(pending)
        attempted = set(pending)
        for peer in dialog_peers:
            key = str(peer)
            if peer in attempted:
                contact = await self.by_legacy_id(key, fetched.get(peer), True)
            else:
                contact = await self.by_legacy_id(key)
            contact.is_friend = True
            ready.append(contact)

        await session.refresh_presence([int(contact.legacy_id) for contact in ready])
        for contact in ready:
            if contact.is_friend:
                contact.apply_presence()
            yield contact

    async def _fetch_users(self, peer_ids: list[int]) -> dict[int, User]:
        client = self.session.client
        if client is None or not peer_ids:
            return {}
        try:
            found = await client.get_users(peer_ids)
        except Exception:
            log.debug("get_users(%s) failed", peer_ids, exc_info=True)
            return {}
        indexed: dict[int, User] = {}
        for user in found:
            ident = user_id(user)
            if ident is not None:
                indexed[ident] = user
        return indexed
