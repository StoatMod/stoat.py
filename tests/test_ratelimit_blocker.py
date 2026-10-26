from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import errno
import typing

from multidict import CIMultiDict
import pytest
import stoat
from stoat import routes


class FakeResponse:
    method = 'GET'
    status = 200

    def __init__(self) -> None:
        self.headers = CIMultiDict()

    async def json(self):
        return {}

    async def text(self) -> str:
        return '{}'

    async def read(self) -> bytes:
        return b'{}'

    def close(self) -> None:
        pass


class FakeHTTPClient(stoat.HTTPClient):
    __slots__ = ('_send',)

    def __init__(self, send: Callable[[], Awaitable[FakeResponse]]) -> None:
        super().__init__('token', state=stoat.Client(token='token').state)
        self._send = send

    async def send_request(
        self,
        method: str,
        url: str,
        *,
        headers: CIMultiDict[typing.Any],
        overrides: typing.Optional[stoat.HTTPOverrideOptions] = None,
        **kwargs,
    ) -> stoat.HTTPResponse:
        del method, url, headers, overrides, kwargs
        return await self._send()  # type: ignore[return-value]


def make_http(send: Callable[[], Awaitable[FakeResponse]]) -> stoat.HTTPClient:
    return FakeHTTPClient(send)


@pytest.mark.asyncio
async def test_cancelled_request_releases_route_blocker():
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(3600)  # a request that never gets a response
        return FakeResponse()

    http = make_http(send)
    route = routes.USERS_EDIT_SELF_USER.compile()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(http.raw_request(route), timeout=0.05)

    # Before the fix the blocker stayed acquired and this waited forever.
    response = await asyncio.wait_for(http.raw_request(route), timeout=1)
    assert response.status == 200


@pytest.mark.asyncio
async def test_connection_reset_retry_does_not_wait_on_its_own_blocker():
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')
        return FakeResponse()

    http = make_http(send)
    route = routes.USERS_EDIT_SELF_USER.compile()

    # Before the fix the retry re-acquired the blocker it still held and never finished.
    response = await asyncio.wait_for(http.raw_request(route), timeout=5)
    assert response.status == 200
    assert calls == 2
