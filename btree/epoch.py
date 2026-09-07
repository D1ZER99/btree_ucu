"""Epoch-based reader tracking for safe page reclamation."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Optional


class EpochTracker:
    """
    Readers pin the current epoch on enter and unpin on leave.
    The writer bumps the epoch on each successful commit.
    Pages retired at epoch R may be reused when no reader is pinned
    at an epoch <= R (i.e. min_pinned is None or min_pinned > R).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._epoch = 0
        self._active: Counter[int] = Counter()

    def current(self) -> int:
        with self._lock:
            return self._epoch

    def enter(self) -> int:
        with self._lock:
            e = self._epoch
            self._active[e] += 1
            return e

    def leave(self, pinned: int) -> None:
        with self._lock:
            self._active[pinned] -= 1
            if self._active[pinned] <= 0:
                del self._active[pinned]

    def bump(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch

    def min_pinned(self) -> Optional[int]:
        with self._lock:
            if not self._active:
                return None
            return min(self._active)

    def is_safe_to_reclaim(self, retire_epoch: int) -> bool:
        pinned = self.min_pinned()
        return pinned is None or pinned > retire_epoch
