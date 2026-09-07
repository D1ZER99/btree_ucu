"""Unit tests against the in-memory page backend."""

from __future__ import annotations

import pytest

from btree import KVStore
from btree.node import deserialize_node, InternalNode, LeafNode
from btree.page_store import MemoryPageStore


def test_insert_and_get():
    db = KVStore(page_size=4096)
    db.put(b"a", b"1")
    db.put(b"b", b"2")
    assert db.get(b"a") == b"1"
    assert db.get(b"b") == b"2"
    assert db.get(b"missing") is None
    db.close()


def test_upsert_overwrites():
    db = KVStore()
    db.put(b"k", b"v1")
    db.put(b"k", b"v2")
    assert db.get(b"k") == b"v2"
    db.close()


def test_lexicographic_order_lookups():
    db = KVStore(page_size=512)
    for i in range(50):
        db.put(f"key{i:03d}".encode(), f"val{i}".encode())
    for i in range(50):
        assert db.get(f"key{i:03d}".encode()) == f"val{i}".encode()
    db.close()


def test_leaf_split():
    # Tiny pages force leaf splits quickly.
    db = KVStore(page_size=256)
    n = 40
    for i in range(n):
        db.put(f"k{i:04d}".encode(), b"x" * 8)
    for i in range(n):
        assert db.get(f"k{i:04d}".encode()) == b"x" * 8

    store = db._store
    root = deserialize_node(store.read_page(store.get_header().root_page_id))
    assert isinstance(root, InternalNode) or (
        isinstance(root, LeafNode) and len(root.entries) < n
    )
    # With page_size 256 and 40 entries, root should be internal after splits.
    assert isinstance(root, InternalNode)
    db.close()


def test_internal_split_and_multi_level():
    db = KVStore(page_size=256)
    n = 200
    for i in range(n):
        db.put(f"k{i:05d}".encode(), b"v" * 4)
    for i in range(n):
        assert db.get(f"k{i:05d}".encode()) == b"v" * 4

    store = db._store
    root_id = store.get_header().root_page_id
    root = deserialize_node(store.read_page(root_id))
    assert isinstance(root, InternalNode)

    # Walk one path; if any child is internal, tree height >= 3.
    child = deserialize_node(store.read_page(root.children[0]))
    height_at_least_3 = isinstance(child, InternalNode)
    # 200 entries on 256-byte pages should produce height >= 3.
    assert height_at_least_3
    db.close()


def test_custom_memory_backend_injection():
    backend = MemoryPageStore(page_size=512)
    db = KVStore(backend=backend)
    db.put(b"x", b"y")
    assert db.get(b"x") == b"y"
    db.close()


def test_empty_value_and_key():
    db = KVStore()
    db.put(b"", b"")
    assert db.get(b"") == b""
    db.put(b"", b"nonempty")
    assert db.get(b"") == b"nonempty"
    db.close()


def test_type_errors():
    db = KVStore()
    with pytest.raises(TypeError):
        db.put("str", b"v")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        db.get("str")  # type: ignore[arg-type]
    db.close()
