"""Testes do parser ISAPI (sem câmera, com fixtures XML)."""

from __future__ import annotations

import pytest

from isapi_event_vision.parser import (
    AlertParseError,
    parse_alert,
    split_multipart,
)

ALERT_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <channelID>1</channelID>
  <dateTime>2026-07-09T12:00:00-03:00</dateTime>
  <eventType>VMD</eventType>
  <eventState>active</eventState>
</EventNotificationAlert>"""

ALERT_NO_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert>
  <dynChannelID>3</dynChannelID>
  <eventType>linedetection</eventType>
</EventNotificationAlert>"""


def test_parse_with_namespace() -> None:
    ev = parse_alert(ALERT_NS)
    assert ev.event_type == "VMD"
    assert ev.channel_id == 1
    assert ev.event_state == "active"
    assert ev.date_time is not None


def test_parse_without_namespace_and_dynchannel() -> None:
    ev = parse_alert(ALERT_NO_NS)
    assert ev.event_type == "linedetection"
    assert ev.channel_id == 3
    assert ev.event_state == "active"  # default
    assert ev.date_time is None


def test_invalid_xml_raises() -> None:
    with pytest.raises(AlertParseError):
        parse_alert(b"<not-closed>")


def test_missing_required_fields_raises() -> None:
    with pytest.raises(AlertParseError):
        parse_alert(b"<EventNotificationAlert><eventType>x</eventType></EventNotificationAlert>")


def test_non_numeric_channel_raises() -> None:
    xml = (
        b"<EventNotificationAlert><eventType>x</eventType>"
        b"<channelID>abc</channelID></EventNotificationAlert>"
    )
    with pytest.raises(AlertParseError):
        parse_alert(xml)


def test_split_multipart_extracts_payloads() -> None:
    body = (
        b"--boundary\r\n"
        b"Content-Type: application/xml\r\n\r\n"
        b"<a>1</a>\r\n"
        b"--boundary\r\n"
        b"Content-Type: application/xml\r\n\r\n"
        b"<b>2</b>\r\n"
        b"--boundary--\r\n"
    )
    parts = split_multipart(body, "boundary")
    assert parts == [b"<a>1</a>", b"<b>2</b>"]
