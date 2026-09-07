"""Copy-on-write B+-tree put/get with path copying and splits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .epoch import EpochTracker
from .freelist import FreeList
from .node import InternalNode, LeafNode, deserialize_node
from .page_store import PageStore


@dataclass
class _PutResult:
    """Result of a COW put on a subtree."""

    page_id: int
    # If the subtree root split, separator and right sibling must be
    # absorbed by the parent (or create a new tree root).
    split_sep: Optional[bytes] = None
    split_right: Optional[int] = None
    retired: List[int] = field(default_factory=list)


class CowBTree:
    def __init__(
        self,
        store: PageStore,
        epochs: EpochTracker,
        freelist: FreeList,
    ) -> None:
        self._store = store
        self._epochs = epochs
        self._freelist = freelist
        self._page_size = store.page_size()
        self._ensure_root_initialized()

    def _ensure_root_initialized(self) -> None:
        header = self._store.get_header()
        root_data = self._store.read_page(header.root_page_id)
        if root_data[0] not in (1, 2):
            leaf = LeafNode()
            self._store.write_page(
                header.root_page_id, leaf.serialize(self._page_size)
            )

    def _load(self, page_id: int) -> LeafNode | InternalNode:
        return deserialize_node(self._store.read_page(page_id))

    def _write_new(self, node: LeafNode | InternalNode) -> int:
        page_id = self._freelist.allocate()
        data = node.serialize(self._page_size)
        self._store.write_page(page_id, data)
        return page_id

    def get(self, key: bytes) -> Optional[bytes]:
        pinned = self._epochs.enter()
        try:
            page_id = self._store.get_header().root_page_id
            while True:
                node = self._load(page_id)
                if isinstance(node, LeafNode):
                    return node.get(key)
                page_id = node.children[node.child_index(key)]
        finally:
            self._epochs.leave(pinned)

    def put(self, key: bytes, value: bytes) -> None:
        root_id = self._store.get_header().root_page_id
        result = self._cow_put(root_id, key, value)
        if result.split_sep is not None:
            assert result.split_right is not None
            new_root = InternalNode(
                keys=[result.split_sep],
                children=[result.page_id, result.split_right],
            )
            new_root_id = self._write_new(new_root)
        else:
            new_root_id = result.page_id

        header = self._store.get_header()
        header.root_page_id = new_root_id
        self._store.put_header(header)
        retire_epoch = self._epochs.bump()
        self._freelist.retire(result.retired, retire_epoch)

    def _cow_put(self, page_id: int, key: bytes, value: bytes) -> _PutResult:
        node = self._load(page_id)
        retired: List[int] = [page_id]

        if isinstance(node, LeafNode):
            return self._cow_put_leaf(node, key, value, retired)

        assert isinstance(node, InternalNode)
        return self._cow_put_internal(node, key, value, retired)

    def _cow_put_leaf(
        self,
        node: LeafNode,
        key: bytes,
        value: bytes,
        retired: List[int],
    ) -> _PutResult:
        new_leaf = LeafNode(list(node.entries))
        new_leaf.upsert(key, value)

        if new_leaf.serialized_size() <= self._page_size:
            return _PutResult(page_id=self._write_new(new_leaf), retired=retired)

        if len(new_leaf.entries) < 2:
            raise ValueError(
                "key/value pair does not fit in a single page"
            )
        left, sep, right = new_leaf.split()
        if (
            left.serialized_size() > self._page_size
            or right.serialized_size() > self._page_size
        ):
            raise ValueError(
                "key/value pair does not fit in a single page after split"
            )
        left_id = self._write_new(left)
        right_id = self._write_new(right)
        return _PutResult(
            page_id=left_id,
            split_sep=sep,
            split_right=right_id,
            retired=retired,
        )

    def _cow_put_internal(
        self,
        node: InternalNode,
        key: bytes,
        value: bytes,
        retired: List[int],
    ) -> _PutResult:
        idx = node.child_index(key)
        child_result = self._cow_put(node.children[idx], key, value)
        retired.extend(child_result.retired)

        new_internal = InternalNode(
            keys=list(node.keys), children=list(node.children)
        )
        new_internal.children[idx] = child_result.page_id
        if child_result.split_sep is not None:
            assert child_result.split_right is not None
            new_internal.insert_separator(
                idx, child_result.split_sep, child_result.split_right
            )

        if new_internal.serialized_size() <= self._page_size:
            return _PutResult(
                page_id=self._write_new(new_internal), retired=retired
            )

        if len(new_internal.keys) < 2:
            raise ValueError("internal node cannot split with fewer than 2 keys")
        left, sep, right = new_internal.split()
        left_id = self._write_new(left)
        right_id = self._write_new(right)
        return _PutResult(
            page_id=left_id,
            split_sep=sep,
            split_right=right_id,
            retired=retired,
        )
