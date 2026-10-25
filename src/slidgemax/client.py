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

"""Auth policy around PyMax login.

PyMax 2.5.0 ``Client.start()`` raises on a revoked login token when
``ExtraConfig.relogin`` is false, and reconnects only on network errors.
Unattended sessions still refuse SMS, so a missing session file cannot fall
through to ``ConsoleSmsCodeProvider``. Registration may drop a revoked session
and request SMS once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from pymax.exceptions import ApiError
from pymax.protocol.enums import Opcode

if TYPE_CHECKING:
    from pymax import Client
    from pymax.app import App
    from pymax.auth.models import AuthResult

log = logging.getLogger(__name__)

REVOKED_LOGIN_MESSAGE = (
    "MAX login token was revoked. Register again. "
    "This gateway will not request an SMS code on its own."
)

_REVOKED_ERRORS = ("FAIL_LOGIN_TOKEN", "FAIL_LOGOUT_ALL")


class LoginTokenRevoked(RuntimeError):
    """Saved MAX login token can no longer be used."""


class RefuseSmsAuth:
    """Auth flow that fails before ``request_code()``.

    Used for an already registered session. A missing or deleted session file
    must not fall through to ``ConsoleSmsCodeProvider``.
    """

    async def authenticate(self, app: App) -> AuthResult:
        raise LoginTokenRevoked(REVOKED_LOGIN_MESSAGE)


def is_revoked_login_token(exc: BaseException) -> bool:
    return (
        isinstance(exc, ApiError)
        and exc.opcode == Opcode.LOGIN
        and any(err in (exc.error, exc.message) for err in _REVOKED_ERRORS)
    )


async def start_client(client: Client) -> None:
    try:
        await client.start()
    except ApiError as exc:
        if is_revoked_login_token(exc):
            log.error("MAX login token revoked; not requesting a new SMS code")
            raise LoginTokenRevoked(REVOKED_LOGIN_MESSAGE) from exc
        raise


async def start_once_or_recover(
    client: Client,
    recover: Callable[[], bool],
) -> None:
    """Run ``start()``. On a revoked token, drop the saved session and try once more.

    ``recover`` is checked when the token failure happens. A long-lived client
    that already logged in must pass a callback that returns false.
    """
    try:
        await start_client(client)
    except LoginTokenRevoked:
        if not recover():
            raise
        log.warning("MAX login token revoked during registration; requesting SMS once")
        try:
            await client.relogin()
        except ApiError as exc:
            if is_revoked_login_token(exc):
                raise LoginTokenRevoked(REVOKED_LOGIN_MESSAGE) from exc
            raise
