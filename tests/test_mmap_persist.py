"""Integration tests against the mmap file backend."""

from __future__ import annotations

import os

from btree import KVStore


def test_persist_and_reopen(tmp_path):
    path = str(tmp_path / "store.db")
    db = KVStore(path, page_size=4096)
    db.put(b"alpha", b"one")
    db.put(b"beta", b"two")
    db.put(b"alpha", b"updated")
    db.close()

    assert os.path.exists(path)
    assert os.path.getsize(path) > 0

    db2 = KVStore(path)
    assert db2.get(b"alpha") == b"updated"
    assert db2.get(b"beta") == b"two"
    assert db2.get(b"gamma") is None
    db2.close()


def test_persist_after_splits(tmp_path):
    path = str(tmp_path / "split.db")
    db = KVStore(path, page_size=256)
    n = 100
    for i in range(n):
        db.put(f"k{i:04d}".encode(), f"v{i}".encode())
    db.close()

    db2 = KVStore(path)
    for i in range(n):
        assert db2.get(f"k{i:04d}".encode()) == f"v{i}".encode()
    db2.close()


def test_context_manager(tmp_path):
    path = str(tmp_path / "ctx.db")
    with KVStore(path) as db:
        db.put(b"a", b"b")
    with KVStore(path) as db:
        assert db.get(b"a") == b"b"
