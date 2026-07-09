"""Parser do alarm stream Hikvision ISAPI.

O endpoint `/ISAPI/Event/notification/alertStream` devolve `multipart/mixed`,
cada parte um XML `<EventNotificationAlert>`. Firmwares variam (namespace
presente ou não, `channelID` vs `dynChannelID`), então o parser é custom e
tolerante — e puro (bytes → modelo), o que o torna testável sem câmera e alvo
natural de fuzzing (atheris) nas próximas etapas.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

# Namespace comum nos firmwares Hikvision (nem sempre presente).
ISAPI_NS = "{http://www.hikvision.com/ver20/XMLSchema}"


class AlertEvent(BaseModel):
    """Um evento do alarm stream, normalizado."""

    event_type: str = Field(min_length=1)
    channel_id: int = Field(ge=0)
    event_state: str = "active"
    date_time: str | None = None


class AlertParseError(ValueError):
    """XML inválido ou campos obrigatórios ausentes."""


def split_multipart(body: bytes, boundary: str) -> list[bytes]:
    """Quebra um corpo multipart/mixed nos payloads XML (headers removidos)."""
    delimiter = b"--" + boundary.encode()
    parts: list[bytes] = []
    for raw in body.split(delimiter):
        chunk = raw.strip()
        if not chunk or chunk == b"--":
            continue
        # Separa headers do payload pela linha em branco.
        for sep in (b"\r\n\r\n", b"\n\n"):
            if sep in chunk:
                chunk = chunk.split(sep, 1)[1]
                break
        parts.append(chunk.strip())
    return parts


def parse_alert(xml_bytes: bytes) -> AlertEvent:
    """Parseia um `<EventNotificationAlert>` para `AlertEvent`."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AlertParseError(f"XML inválido: {exc}") from exc

    def find(tag: str) -> str | None:
        el = root.find(f"{ISAPI_NS}{tag}")
        if el is None:
            el = root.find(tag)
        return el.text if el is not None else None

    event_type = find("eventType")
    channel = find("channelID") or find("dynChannelID")
    if event_type is None or channel is None:
        raise AlertParseError("faltando eventType e/ou channelID")
    try:
        channel_id = int(channel)
    except ValueError as exc:
        raise AlertParseError(f"channelID não numérico: {channel!r}") from exc

    return AlertEvent(
        event_type=event_type,
        channel_id=channel_id,
        event_state=find("eventState") or "active",
        date_time=find("dateTime"),
    )
