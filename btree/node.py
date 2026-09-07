"""B+-tree node encoding, decoding, and binary search."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

NODE_LEAF = 1
NODE_INTERNAL = 2

# type(u8) count(u16)
_NODE_HDR = struct.Struct("<BH")


@dataclass
class LeafNode:
    entries: List[Tuple[bytes, bytes]] = field(default_factory=list)

    @property
    def node_type(self) -> int:
        return NODE_LEAF

    def find(self, key: bytes) -> Tuple[int, bool]:
        """Return (index, found). Index is insertion point if not found."""
        lo, hi = 0, len(self.entries)
        while lo < hi:
            mid = (lo + hi) // 2
            mk = self.entries[mid][0]
            if mk < key:
                lo = mid + 1
            elif mk > key:
                hi = mid
            else:
                return mid, True
        return lo, False

    def get(self, key: bytes) -> Optional[bytes]:
        idx, found = self.find(key)
        if found:
            return self.entries[idx][1]
        return None

    def upsert(self, key: bytes, value: bytes) -> None:
        idx, found = self.find(key)
        if found:
            self.entries[idx] = (key, value)
        else:
            self.entries.insert(idx, (key, value))

    def serialized_size(self) -> int:
        size = _NODE_HDR.size
        for key, value in self.entries:
            size += 2 + len(key) + 2 + len(value)
        return size

    def serialize(self, page_size: int) -> bytes:
        size = self.serialized_size()
        if size > page_size:
            raise ValueError(
                f"leaf node size {size} exceeds page size {page_size}"
            )
        parts = [_NODE_HDR.pack(NODE_LEAF, len(self.entries))]
        for key, value in self.entries:
            parts.append(struct.pack("<H", len(key)))
            parts.append(key)
            parts.append(struct.pack("<H", len(value)))
            parts.append(value)
        data = b"".join(parts)
        return data

    @classmethod
    def deserialize(cls, data: bytes) -> LeafNode:
        node_type, count = _NODE_HDR.unpack_from(data)
        if node_type != NODE_LEAF:
            raise ValueError(f"expected leaf node, got type {node_type}")
        offset = _NODE_HDR.size
        entries: List[Tuple[bytes, bytes]] = []
        for _ in range(count):
            (klen,) = struct.unpack_from("<H", data, offset)
            offset += 2
            key = data[offset : offset + klen]
            offset += klen
            (vlen,) = struct.unpack_from("<H", data, offset)
            offset += 2
            value = data[offset : offset + vlen]
            offset += vlen
            entries.append((key, value))
        return cls(entries)

    def split(self) -> Tuple[LeafNode, bytes, LeafNode]:
        """Split into left, separator (first key of right), right."""
        mid = len(self.entries) // 2
        if mid == 0:
            raise ValueError("cannot split leaf with fewer than 2 entries")
        left = LeafNode(self.entries[:mid])
        right = LeafNode(self.entries[mid:])
        separator = right.entries[0][0]
        return left, separator, right


@dataclass
class InternalNode:
    """B+ internal node: len(children) == len(keys) + 1."""

    keys: List[bytes] = field(default_factory=list)
    children: List[int] = field(default_factory=list)

    @property
    def node_type(self) -> int:
        return NODE_INTERNAL

    def child_index(self, key: bytes) -> int:
        """Index of child that may contain key."""
        lo, hi = 0, len(self.keys)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.keys[mid] <= key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def serialized_size(self) -> int:
        size = _NODE_HDR.size
        size += 4 * len(self.children)
        for key in self.keys:
            size += 2 + len(key)
        return size

    def serialize(self, page_size: int) -> bytes:
        if len(self.children) != len(self.keys) + 1:
            raise ValueError("internal node children/keys invariant broken")
        size = self.serialized_size()
        if size > page_size:
            raise ValueError(
                f"internal node size {size} exceeds page size {page_size}"
            )
        parts = [_NODE_HDR.pack(NODE_INTERNAL, len(self.keys))]
        for child in self.children:
            parts.append(struct.pack("<I", child))
        for key in self.keys:
            parts.append(struct.pack("<H", len(key)))
            parts.append(key)
        return b"".join(parts)

    @classmethod
    def deserialize(cls, data: bytes) -> InternalNode:
        node_type, count = _NODE_HDR.unpack_from(data)
        if node_type != NODE_INTERNAL:
            raise ValueError(f"expected internal node, got type {node_type}")
        offset = _NODE_HDR.size
        children: List[int] = []
        for _ in range(count + 1):
            (child,) = struct.unpack_from("<I", data, offset)
            offset += 4
            children.append(child)
        keys: List[bytes] = []
        for _ in range(count):
            (klen,) = struct.unpack_from("<H", data, offset)
            offset += 2
            key = data[offset : offset + klen]
            offset += klen
            keys.append(key)
        return cls(keys=keys, children=children)

    def insert_separator(
        self, index: int, separator: bytes, right_child: int
    ) -> None:
        """After splitting child at index, insert separator and right sibling."""
        self.keys.insert(index, separator)
        self.children.insert(index + 1, right_child)

    def split(self) -> Tuple[InternalNode, bytes, InternalNode]:
        """Split; promote middle key. Left has mid keys, right has rest."""
        mid = len(self.keys) // 2
        if mid == 0:
            raise ValueError("cannot split internal with fewer than 2 keys")
        separator = self.keys[mid]
        left = InternalNode(
            keys=self.keys[:mid],
            children=self.children[: mid + 1],
        )
        right = InternalNode(
            keys=self.keys[mid + 1 :],
            children=self.children[mid + 1 :],
        )
        return left, separator, right


def deserialize_node(data: bytes) -> LeafNode | InternalNode:
    node_type = data[0]
    if node_type == NODE_LEAF:
        return LeafNode.deserialize(data)
    if node_type == NODE_INTERNAL:
        return InternalNode.deserialize(data)
    raise ValueError(f"unknown node type {node_type}")
