"""아침 보고서 (계약 12절). 실행 진입점은 `python -m stocker.report`."""

from __future__ import annotations

import os

from dotenv import dotenv_values


def env(name: str, default: str | None = None) -> str | None:
    """프로세스 환경변수 → `.env` 순으로 읽는다. 빈 문자열은 없음으로 취급.

    `Settings`(config.py)는 보고서용 변수를 모르므로(extra="ignore") 이 패키지가 직접 읽는다.
    """
    value = os.environ.get(name) or dotenv_values(".env").get(name)
    return value or default
