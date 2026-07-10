"""Harness atheris para o parser ISAPI (lógica pura → alvo natural de fuzzing).

Contrato sob teste: `parse_alert` deve, para QUALQUER entrada, ou devolver um
`AlertEvent` válido ou levantar `AlertParseError` — nunca vazar outra exceção
(pydantic `ValidationError`, `TypeError`, etc.). `split_multipart` nunca deve
quebrar. Qualquer exceção fora do contrato aborta o fuzzer com o input mínimo.

Uso:
    uv run python fuzz/fuzz_parser.py -atheris_runs=200000
    uv run python fuzz/fuzz_parser.py corpus/          # replay de um corpus

Fuzzing é time-bound (roda em nightly, não no CI de PR). Crashes encontrados
viram testes de regressão em tests/unit/test_parser_regressions.py.
"""

from __future__ import annotations

import contextlib
import sys

import atheris

with atheris.instrument_imports():
    from isapi_event_vision.parser import (
        AlertParseError,
        parse_alert,
        split_multipart,
    )


def _test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    boundary = fdp.ConsumeUnicodeNoSurrogates(16) or "b"

    # split_multipart: transporte puro, não deve levantar nada.
    for part in split_multipart(data, boundary):
        _drive_parse(part)

    # parse_alert direto sobre os bytes crus também.
    _drive_parse(data)


def _drive_parse(payload: bytes) -> None:
    # AlertParseError é o único erro previsto pelo contrato; qualquer outra
    # exceção sobe e aborta o fuzzer com o input mínimo.
    with contextlib.suppress(AlertParseError):
        parse_alert(payload)


def main() -> None:
    atheris.Setup(sys.argv, _test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
