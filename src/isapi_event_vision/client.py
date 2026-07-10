"""Cliente httpx do alarm stream Hikvision ISAPI (Digest auth, streaming incremental).

O endpoint `/ISAPI/Event/notification/alertStream` é um `multipart/mixed`
potencialmente infinito. Este cliente NÃO bufferiza o corpo inteiro: reagrupa os
bytes que chegam e emite cada parte XML assim que o próximo boundary a fecha,
delegando ao parser puro (`parse_alert`). Todo o I/O fica isolado aqui — a lógica
de fronteira (`_iter_parts`) é pura sobre um fluxo de bytes e testável com respx,
sem câmera.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import TracebackType

import httpx

from isapi_event_vision.parser import (
    AlertEvent,
    AlertParseError,
    parse_alert,
    strip_part_headers,
)

logger = logging.getLogger(__name__)

ALERT_STREAM_PATH = "/ISAPI/Event/notification/alertStream"

# Teto de uma única parte ainda não fechada. Eventos ISAPI são pequenos (poucos
# KB); uma parte que cresce além disso sem boundary de fecho indica corpo
# malformado ou câmera hostil tentando exaurir memória — abortamos em vez de
# bufferizar sem limite.
MAX_PART_BYTES = 1 << 20  # 1 MiB


class AlarmStreamError(RuntimeError):
    """Falha de transporte/protocolo ao consumir o alarm stream."""


def _extract_boundary(content_type: str) -> str:
    """Extrai o token de boundary de `multipart/mixed; boundary=...`.

    O nome do parâmetro é case-insensitive (RFC 2045); o valor não. Um `=` no
    valor (comum em boundaries base64) é preservado.
    """
    for token in content_type.split(";"):
        name, sep, value = token.strip().partition("=")
        if sep and name.lower() == "boundary":
            boundary = value.strip().strip('"')
            if boundary:
                return boundary
    raise AlarmStreamError(f"Content-Type sem boundary utilizável: {content_type!r}")


async def _iter_parts(
    chunks: AsyncIterator[bytes],
    boundary: str,
    *,
    max_part_bytes: int = MAX_PART_BYTES,
) -> AsyncIterator[bytes]:
    """Reagrupa um fluxo de bytes multipart nos payloads XML, um a um.

    Só emite uma parte quando o boundary seguinte já chegou (a parte está
    fechada), então funciona para corpo finito e stream infinito. O buffer
    retém apenas a parte ainda aberta; se ela passar de `max_part_bytes` sem
    fechar, aborta (proteção contra memória ilimitada com input não-confiável).
    """
    delimiter = b"--" + boundary.encode()
    dlen = len(delimiter)
    buffer = b""
    async for chunk in chunks:
        buffer += chunk
        pos = buffer.find(delimiter)
        while pos != -1:
            nxt = buffer.find(delimiter, pos + dlen)
            if nxt == -1:
                break
            payload = strip_part_headers(buffer[pos + dlen : nxt])
            if payload:
                yield payload
            pos = nxt
        if pos != -1:
            # Descarta preâmbulo/partes já emitidas; mantém a parte aberta.
            buffer = buffer[pos:]
        elif len(buffer) >= dlen:
            # Sem delimiter: retém só o bastante p/ um delimiter partido entre
            # chunks; o resto é preâmbulo descartável.
            buffer = buffer[-(dlen - 1) :] if dlen > 1 else b""
        if len(buffer) > max_part_bytes:
            raise AlarmStreamError(
                f"parte do alarm stream excede {max_part_bytes} bytes sem boundary de fecho"
            )


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
                # Uma parte inválida (malformada ou ataque de entidade barrado
                # pelo defusedxml) não pode derrubar o stream inteiro: loga e
                # segue. Erro nunca em silêncio (warning), nunca fatal.
                try:
                    event = parse_alert(payload)
                except AlertParseError as exc:
                    logger.warning("alarm stream: descartando parte inválida (%s)", exc)
                    continue
                yield event
