"""Cliente httpx do alarm stream Hikvision ISAPI (Digest auth, streaming incremental).

O endpoint `/ISAPI/Event/notification/alertStream` é um `multipart/mixed`
potencialmente infinito. Este cliente NÃO bufferiza o corpo inteiro: reagrupa os
bytes que chegam e emite cada parte XML assim que o próximo boundary a fecha,
delegando ao parser puro (`parse_alert`). Todo o I/O fica isolado aqui — a lógica
de fronteira (`_iter_parts`) é pura sobre um fluxo de bytes e testável com respx,
sem câmera.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType

import httpx

from isapi_event_vision.parser import AlertEvent, parse_alert

ALERT_STREAM_PATH = "/ISAPI/Event/notification/alertStream"


class AlarmStreamError(RuntimeError):
    """Falha de transporte/protocolo ao consumir o alarm stream."""


def _extract_boundary(content_type: str) -> str:
    """Extrai o token de boundary de `multipart/mixed; boundary=...`."""
    for token in content_type.split(";"):
        token = token.strip()
        if token.startswith("boundary="):
            boundary = token[len("boundary=") :].strip('"')
            if boundary:
                return boundary
    raise AlarmStreamError(f"Content-Type sem boundary utilizável: {content_type!r}")


def _strip_part_headers(segment: bytes) -> bytes:
    """Remove os headers MIME de uma parte, devolvendo só o payload (ou vazio)."""
    chunk = segment.strip()
    if not chunk or chunk == b"--":
        return b""
    for sep in (b"\r\n\r\n", b"\n\n"):
        if sep in chunk:
            return chunk.split(sep, 1)[1].strip()
    return chunk


async def _iter_parts(chunks: AsyncIterator[bytes], boundary: str) -> AsyncIterator[bytes]:
    """Reagrupa um fluxo de bytes multipart nos payloads XML, um a um.

    Só emite uma parte quando o boundary seguinte já chegou (a parte está fechada),
    então funciona tanto para um corpo finito quanto para um stream infinito.
    """
    delimiter = b"--" + boundary.encode()
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        while True:
            start = buffer.find(delimiter)
            if start == -1:
                break
            nxt = buffer.find(delimiter, start + len(delimiter))
            if nxt == -1:
                # Parte ainda aberta: descarta o preâmbulo e espera mais bytes.
                buffer = buffer[start:]
                break
            payload = _strip_part_headers(buffer[start + len(delimiter) : nxt])
            buffer = buffer[nxt:]
            if payload:
                yield payload


class AlarmStreamClient:
    """Consome o alarm stream ISAPI com autenticação Digest.

    O timeout default é desabilitado (`None`) porque o stream é de longa duração;
    um read-timeout mataria a conexão entre eventos. Passe um valor só se quiser
    falhar na ausência de tráfego.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify: bool = True,
        timeout: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            auth=httpx.DigestAuth(username, password),
            verify=verify,
            timeout=timeout,
        )

    async def __aenter__(self) -> AlarmStreamClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream_events(self) -> AsyncIterator[AlertEvent]:
        """Conecta ao alertStream e emite cada `AlertEvent` conforme chega."""
        url = f"{self._base_url}{ALERT_STREAM_PATH}"
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            boundary = _extract_boundary(response.headers.get("content-type", ""))
            async for payload in _iter_parts(response.aiter_bytes(), boundary):
                yield parse_alert(payload)
