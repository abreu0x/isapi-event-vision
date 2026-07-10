"""Regressões achadas por fuzzing (fuzz/fuzz_parser.py).

Cada caso é um input que já violou o contrato de `parse_alert` (vazou uma
exceção que não era `AlertParseError`). Ficam aqui como testes rápidos para o
CI de PR, sem depender do atheris.
"""

from __future__ import annotations

import pytest

from isapi_event_vision.parser import AlertParseError, parse_alert

# channelID negativo violava Field(ge=0) e vazava pydantic.ValidationError.
NEGATIVE_CHANNEL = (
    b"<EventNotificationAlert><eventType>x</eventType>"
    b"<channelID>-1</channelID></EventNotificationAlert>"
)

# Billion laughs: expansão de entidade. O alarm stream vem de câmera (input
# não-confiável); defusedxml deve barrar e virar AlertParseError, sem expandir.
BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
    b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
    b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">]>\n'
    b"<EventNotificationAlert><eventType>&lol3;</eventType>"
    b"<channelID>1</channelID></EventNotificationAlert>"
)


def test_negative_channel_raises_parse_error_not_validation_error() -> None:
    with pytest.raises(AlertParseError):
        parse_alert(NEGATIVE_CHANNEL)


def test_entity_expansion_attack_is_blocked() -> None:
    # Não pode expandir a entidade nem vazar EntitiesForbidden: vira AlertParseError.
    with pytest.raises(AlertParseError):
        parse_alert(BILLION_LAUGHS)
