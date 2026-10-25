from __future__ import annotations

from pathlib import Path

import pytest
from pymax import Client
from pymax.exceptions import ApiError
from pymax.protocol.enums import Opcode

from slidgemax.client import (
    REVOKED_LOGIN_MESSAGE,
    LoginTokenRevoked,
    RefuseSmsAuth,
    is_revoked_login_token,
    start_client,
    start_once_or_recover,
)
from slidgemax.session import make_client


def _revoked() -> ApiError:
    return ApiError(
        opcode=Opcode.LOGIN,
        error="FAIL_LOGIN_TOKEN",
        message="login token was revoked",
    )


def test_revoked_login_token_detector() -> None:
    assert is_revoked_login_token(_revoked())
    assert is_revoked_login_token(
        ApiError(opcode=Opcode.LOGIN, error="FAIL_LOGOUT_ALL", message="logged out")
    )
    assert not is_revoked_login_token(
        ApiError(opcode=Opcode.LOGIN, error="error.limit.violate", message="too many")
    )
    assert not is_revoked_login_token(RuntimeError("FAIL_LOGIN_TOKEN"))


async def test_refuse_sms_auth_does_not_touch_the_client() -> None:
    class Boom:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(name)

    with pytest.raises(LoginTokenRevoked, match="will not request an SMS code"):
        await RefuseSmsAuth().authenticate(Boom())  # type: ignore[arg-type]


def test_unattended_client_cannot_request_sms(tmp_path: Path) -> None:
    client = make_client("+79990000000", tmp_path)
    assert type(client) is Client
    assert isinstance(client._auth_flow, RefuseSmsAuth)
    assert client.extra_config.relogin is False


def test_registration_client_keeps_sms_provider(tmp_path: Path) -> None:
    from slidgemax.auth import QueueSmsProvider

    provider = QueueSmsProvider()
    client = make_client("+79990000000", tmp_path, sms_provider=provider)
    assert type(client) is Client
    assert not isinstance(client._auth_flow, RefuseSmsAuth)
    assert client._auth_flow.code_provider is provider


class _Start:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc
        self.starts = 0
        self.relogins = 0

    async def start(self) -> None:
        self.starts += 1
        if self.exc is not None and self.starts == 1:
            raise self.exc

    async def relogin(self, drop_config_token: bool = True, start: bool = True) -> None:
        self.relogins += 1
        if start:
            await self.start()


async def test_start_client_maps_revoked_token() -> None:
    client = _Start(_revoked())
    with pytest.raises(LoginTokenRevoked, match=REVOKED_LOGIN_MESSAGE) as raised:
        await start_client(client)  # type: ignore[arg-type]
    assert raised.value.__cause__ is client.exc
    assert client.starts == 1
    assert client.relogins == 0


async def test_start_client_reraises_other_api_errors() -> None:
    client = _Start(ApiError(opcode=Opcode.LOGIN, error="error.limit.violate", message="too many"))
    with pytest.raises(ApiError, match="too many"):
        await start_client(client)  # type: ignore[arg-type]
    assert client.relogins == 0


async def test_registration_recovery_is_one_shot() -> None:
    client = _Start(_revoked())
    await start_once_or_recover(client, lambda: True)  # type: ignore[arg-type]
    assert client.starts == 2
    assert client.relogins == 1

    client = _Start(_revoked())
    with pytest.raises(LoginTokenRevoked):
        await start_once_or_recover(client, lambda: False)  # type: ignore[arg-type]
    assert client.starts == 1
    assert client.relogins == 0


async def test_registration_recovery_does_not_relogin_other_errors() -> None:
    client = _Start(ApiError(opcode=Opcode.LOGIN, error="error.limit.violate", message="too many"))
    with pytest.raises(ApiError, match="too many"):
        await start_once_or_recover(client, lambda: True)  # type: ignore[arg-type]
    assert client.relogins == 0


async def test_registration_second_revoke_is_not_retried() -> None:
    class _AlwaysRevoked(_Start):
        async def start(self) -> None:
            self.starts += 1
            raise _revoked()

    client = _AlwaysRevoked()
    with pytest.raises(LoginTokenRevoked, match=REVOKED_LOGIN_MESSAGE):
        await start_once_or_recover(client, lambda: True)  # type: ignore[arg-type]
    assert client.starts == 2
    assert client.relogins == 1
