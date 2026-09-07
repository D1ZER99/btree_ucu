"""Public KVStore facade with single-writer concurrency."""

from __future__ import annotations

import threading
from typing import Optional

from .epoch import EpochTracker
from .freelist import FreeList
from .page_store import MemoryPageStore, MmapPageStore, PageStore
from .tree import CowBTree


class KVStore:
    """
    Persistent ordered key-value store backed by a copy-on-write B+-tree.

    - path=None → in-memory backend (tests / ephemeral use)
    - path="file.db" → memory-mapped file backend
    - backend=... → inject a custom PageStore
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        page_size: int = 4096,
        backend: Optional[PageStore] = None,
    ) -> None:
        if backend is not None:
            self._store: PageStore = backend
            self._owns_store = False
        elif path is None:
            self._store = MemoryPageStore(page_size=page_size)
            self._owns_store = True
        else:
            self._store = MmapPageStore(path, page_size=page_size)
            self._owns_store = True

        self._epochs = EpochTracker()
        self._freelist = FreeList(self._store, self._epochs)
        self._tree = CowBTree(self._store, self._epochs, self._freelist)
        self._writer_lock = threading.Lock()
        self._closed = False

    def put(self, key: bytes, value: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)) or not isinstance(
            value, (bytes, bytearray)
        ):
            raise TypeError("key and value must be bytes")
        key_b = bytes(key)
        value_b = bytes(value)
        with self._writer_lock:
            self._check_open()
            self._tree.put(key_b, value_b)

    def get(self, key: bytes) -> Optional[bytes]:
        if not isinstance(key, (bytes, bytearray)):
            raise TypeError("key must be bytes")
        self._check_open()
        return self._tree.get(bytes(key))

    def file_size(self) -> int:
        self._check_open()
        return self._store.file_size()

    def close(self) -> None:
        with self._writer_lock:
            if self._closed:
                return
            self._closed = True
            if self._owns_store:
                self._store.close()

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("store is closed")

    def __enter__(self) -> KVStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
