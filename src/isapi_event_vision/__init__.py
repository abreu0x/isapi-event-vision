"""isapi-event-vision — consome eventos Hikvision ISAPI e classifica por CV."""

from isapi_event_vision.parser import AlertEvent, AlertParseError, parse_alert, split_multipart

__all__ = ["AlertEvent", "AlertParseError", "parse_alert", "split_multipart"]
__version__ = "0.1.0"
