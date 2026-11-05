"""쿼리 키 기준 TTL 메모리 캐시 (기본 1시간). 프로세스 하나 안에서만 유효하다."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any

DEFAULT_TTL = 3600.0


class TTLCache:
    def __init__(self, ttl: float = DEFAULT_TTL, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[Hashable, tuple[float, Any]] = {}

    def get_or_set(self, key: Hashable, fn: Callable[[], Any]) -> Any:
        """키가 살아 있으면 저장값, 아니면 fn()을 호출해 저장하고 돌려준다.

        같은 키의 계산이 동시에 들어오면 fn이 두 번 돌 수 있지만 결과는 같으므로 허용한다
        (계산 중 전체 잠금을 잡아 다른 키까지 막는 것보다 낫다).
        """
        now = self._clock()
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
        value = fn()
        with self._lock:
            self._store[key] = (now + self.ttl, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


def make_key(name: str, params: dict[str, Any] | None = None) -> tuple:
    """엔드포인트 이름 + 정렬된 쿼리 항목 → 해시 가능한 키."""
    items = tuple(sorted((k, _freeze(v)) for k, v in (params or {}).items()))
    return (name, items)


def _freeze(v: Any) -> Hashable:
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze(x)) for k, x in v.items()))
    if isinstance(v, list | tuple | set):
        return tuple(_freeze(x) for x in v)
    return v
