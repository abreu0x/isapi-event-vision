"""Testes do cliente do alarm stream ISAPI (sem câmera: respx + splitter puro)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from isapi_event_vision.client import (
    ALERT_STREAM_PATH,
    AlarmStreamClient,
    AlarmStreamError,
    _extract_boundary,
    _iter_parts,
)

BASE_URL = "https://cam.local"
STREAM_URL = f"{BASE_URL}{ALERT_STREAM_PATH}"
BOUNDARY = "boundary"

ALERT_A = (
    b"<EventNotificationAlert><eventType>VMD</eventType>"
    b"<channelID>1</channelID></EventNotificationAlert>"
)
ALERT_B = (
    b"<EventNotificationAlert><eventType>linedetection</eventType>"
    b"<dynChannelID>3</dynChannelID></EventNotificationAlert>"
)


def _multipart_body(*payloads: bytes) -> bytes:
    parts = [
        b"--" + BOUNDARY.encode() + b"\r\nContent-Type: application/xml\r\n\r\n" + p + b"\r\n"
        for p in payloads
    ]
    return b"".join(parts) + b"--" + BOUNDARY.encode() + b"--\r\n"


async def _agen(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


# --- _extract_boundary (puro) ------------------------------------------------


def test_extract_boundary_quoted_and_unquoted() -> None:
    assert _extract_boundary("multipart/mixed; boundary=abc") == "abc"
    assert _extract_boundary('multipart/mixed; boundary="abc"') == "abc"


def test_extract_boundary_param_name_is_case_insensitive() -> None:
    # Nome do parâmetro é case-insensitive (RFC 2045); valor com '=' preservado.
    assert _extract_boundary("multipart/mixed; Boundary=abc") == "abc"
    assert _extract_boundary("multipart/mixed; BOUNDARY=ab=cd") == "ab=cd"


def test_extract_boundary_missing_raises() -> None:
    with pytest.raises(AlarmStreamError):
        _extract_boundary("text/plain")


async def test_iter_parts_aborts_oversized_open_part() -> None:
    # Parte aberta que nunca fecha e passa do teto → aborta (não bufferiza sem fim).
    huge = b"--boundary\r\n\r\n" + b"A" * 500
    with pytest.raises(AlarmStreamError, match="excede"):
        [p async for p in _iter_parts(_agen(huge), BOUNDARY, max_part_bytes=64)]


# --- _iter_parts (splitter incremental, puro) --------------------------------


async def test_iter_parts_single_chunk() -> None:
    body = _multipart_body(ALERT_A, ALERT_B)
    parts = [p async for p in _iter_parts(_agen(body), BOUNDARY)]
    assert parts == [ALERT_A, ALERT_B]


async def test_iter_parts_split_across_ugly_chunks() -> None:
    # Corta os bytes no meio de um delimiter e no meio de um payload.
    body = _multipart_body(ALERT_A, ALERT_B)
    third = len(body) // 3
    chunks = _agen(body[:third], body[third : 2 * third], body[2 * third :])
    parts = [p async for p in _iter_parts(chunks, BOUNDARY)]
    assert parts == [ALERT_A, ALERT_B]


async def test_iter_parts_drops_open_trailing_part() -> None:
    # Última parte sem o boundary de fechamento não é emitida (ainda aberta).
    body = b"--boundary\r\n\r\n" + ALERT_A + b"\r\n--boundary\r\n\r\n" + ALERT_B
    parts = [p async for p in _iter_parts(_agen(body), BOUNDARY)]
    assert parts == [ALERT_A]


# --- AlarmStreamClient (respx) ----------------------------------------------


@respx.mock
async def test_stream_events_parses_multipart() -> None:
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": f"multipart/mixed; boundary={BOUNDARY}"},
            content=_multipart_body(ALERT_A, ALERT_B),
        )
    )
    async with AlarmStreamClient(BASE_URL, "admin", "secret") as client:
        events = [ev async for ev in client.stream_events()]

    assert [e.event_type for e in events] == ["VMD", "linedetection"]
    assert [e.channel_id for e in events] == [1, 3]


@respx.mock
async def test_stream_events_skips_invalid_part_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Uma parte-lixo no meio não pode derrubar o stream: emite as boas, loga a ruim.
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": f"multipart/mixed; boundary={BOUNDARY}"},
            content=_multipart_body(ALERT_A, b"<not-well-formed", ALERT_B),
        )
    )
    with caplog.at_level(logging.WARNING):
        async with AlarmStreamClient(BASE_URL, "admin", "secret") as client:
            events = [ev async for ev in client.stream_events()]

    assert [e.event_type for e in events] == ["VMD", "linedetection"]
    assert "descartando parte inválida" in caplog.text


@respx.mock
async def test_digest_auth_challenge_is_answered() -> None:
    route = respx.get(STREAM_URL).mock(
        side_effect=[
            httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Digest realm="ISAPI", qop="auth", '
                        'nonce="abc123", opaque="xyz", algorithm=MD5'
                    )
                },
            ),
            httpx.Response(
                200,
                headers={"Content-Type": f"multipart/mixed; boundary={BOUNDARY}"},
                content=_multipart_body(ALERT_A),
            ),
        ]
    )
    async with AlarmStreamClient(BASE_URL, "admin", "secret") as client:
        events = [ev async for ev in client.stream_events()]

    assert len(events) == 1
    assert route.call_count == 2
    # A segunda requisição carrega o Authorization Digest calculado.
    assert route.calls[-1].request.headers["authorization"].startswith("Digest ")


@respx.mock
async def test_http_error_raises() -> None:
    respx.get(STREAM_URL).mock(return_value=httpx.Response(500))
    async with AlarmStreamClient(BASE_URL, "admin", "secret") as client:
        with pytest.raises(httpx.HTTPStatusError):
            [ev async for ev in client.stream_events()]


@respx.mock
async def test_missing_boundary_raises() -> None:
    respx.get(STREAM_URL).mock(
        return_value=httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"x")
    )
    async with AlarmStreamClient(BASE_URL, "admin", "secret") as client:
        with pytest.raises(AlarmStreamError):
            [ev async for ev in client.stream_events()]
