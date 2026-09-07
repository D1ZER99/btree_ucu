"""Page storage backends: in-memory and memory-mapped file."""

from __future__ import annotations

import mmap
import os
import struct
import threading
from typing import Optional, Protocol, runtime_checkable

MAGIC = b"BTRE"
VERSION = 1
HEADER_PAGE_ID = 0

# Header layout within page 0 (little-endian):
# magic(4) version(u8) pad(3) page_size(u32) root(u32) page_count(u32) freelist_head(u32)
_HEADER_STRUCT = struct.Struct("<4sB3sIIII")
_HEADER_SIZE = _HEADER_STRUCT.size


class Header:
    __slots__ = ("page_size", "root_page_id", "page_count", "freelist_head")

    def __init__(
        self,
        page_size: int,
        root_page_id: int,
        page_count: int,
        freelist_head: int = 0,
    ) -> None:
        self.page_size = page_size
        self.root_page_id = root_page_id
        self.page_count = page_count
        self.freelist_head = freelist_head

    def pack(self) -> bytes:
        return _HEADER_STRUCT.pack(
            MAGIC,
            VERSION,
            b"\x00\x00\x00",
            self.page_size,
            self.root_page_id,
            self.page_count,
            self.freelist_head,
        )

    @classmethod
    def unpack(cls, data: bytes) -> Header:
        magic, version, _pad, page_size, root, page_count, freelist_head = (
            _HEADER_STRUCT.unpack_from(data)
        )
        if magic != MAGIC:
            raise ValueError(f"invalid database magic: {magic!r}")
        if version != VERSION:
            raise ValueError(f"unsupported database version: {version}")
        return cls(page_size, root, page_count, freelist_head)


@runtime_checkable
class PageStore(Protocol):
    def page_size(self) -> int: ...

    def read_page(self, page_id: int) -> bytes: ...

    def write_page(self, page_id: int, data: bytes) -> None: ...

    def alloc_page(self) -> int: ...

    def get_header(self) -> Header: ...

    def put_header(self, header: Header) -> None: ...

    def file_size(self) -> int: ...

    def close(self) -> None: ...


class MemoryPageStore:
    """In-memory page backend for unit tests."""

    def __init__(self, page_size: int = 4096) -> None:
        if page_size < 256:
            raise ValueError("page_size must be at least 256")
        self._page_size = page_size
        self._pages: dict[int, bytearray] = {}
        self._pages[HEADER_PAGE_ID] = bytearray(page_size)
        self._pages[1] = bytearray(page_size)
        header = Header(
            page_size=page_size,
            root_page_id=1,
            page_count=2,
            freelist_head=0,
        )
        self.put_header(header)

    def page_size(self) -> int:
        return self._page_size

    def read_page(self, page_id: int) -> bytes:
        try:
            return bytes(self._pages[page_id])
        except KeyError as exc:
            raise IndexError(f"page {page_id} out of range") from exc

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) > self._page_size:
            raise ValueError(
                f"page data length {len(data)} exceeds page size {self._page_size}"
            )
        buf = bytearray(self._page_size)
        buf[: len(data)] = data
        self._pages[page_id] = buf

    def alloc_page(self) -> int:
        header = self.get_header()
        page_id = header.page_count
        self._pages[page_id] = bytearray(self._page_size)
        header.page_count += 1
        self.put_header(header)
        return page_id

    def get_header(self) -> Header:
        return Header.unpack(self._pages[HEADER_PAGE_ID])

    def put_header(self, header: Header) -> None:
        packed = header.pack()
        buf = bytearray(self._page_size)
        buf[: len(packed)] = packed
        self._pages[HEADER_PAGE_ID] = buf

    def file_size(self) -> int:
        return self.get_header().page_count * self._page_size

    def close(self) -> None:
        self._pages.clear()


class MmapPageStore:
    """Memory-mapped file page backend."""

    def __init__(self, path: str, page_size: int = 4096) -> None:
        if page_size < 256:
            raise ValueError("page_size must be at least 256")
        self._path = path
        self._page_size = page_size
        self._file = None
        self._mm: Optional[mmap.mmap] = None
        # Protects mmap remap so readers never observe a None mapping.
        self._mm_lock = threading.RLock()
        created = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, "a+b")
        if created:
            self._init_new()
        else:
            self._open_existing()

    def _map(self, size: int) -> None:
        """Replace mapping. Caller must hold _mm_lock."""
        if size == 0:
            raise ValueError("cannot mmap empty file")
        self._file.seek(0)
        new_mm = mmap.mmap(self._file.fileno(), size)
        old = self._mm
        self._mm = new_mm
        if old is not None:
            old.close()

    def _ensure_file_size(self, size: int) -> None:
        self._file.seek(0, os.SEEK_END)
        current = self._file.tell()
        if current < size:
            self._file.seek(size - 1)
            self._file.write(b"\x00")
            self._file.flush()

    def _init_new(self) -> None:
        initial = 2 * self._page_size
        self._ensure_file_size(initial)
        with self._mm_lock:
            self._map(initial)
            header = Header(
                page_size=self._page_size,
                root_page_id=1,
                page_count=2,
                freelist_head=0,
            )
            self._put_header_unlocked(header)

    def _open_existing(self) -> None:
        self._file.seek(0, os.SEEK_END)
        size = self._file.tell()
        if size < _HEADER_SIZE:
            raise ValueError("database file too small")
        with self._mm_lock:
            self._map(size)
            header = self._get_header_unlocked()
            self._page_size = header.page_size
            expected = header.page_count * self._page_size
            if size < expected:
                raise ValueError("database file truncated")
            if size != expected:
                self._map(expected)

    def page_size(self) -> int:
        return self._page_size

    def _get_header_unlocked(self) -> Header:
        assert self._mm is not None
        return Header.unpack(self._mm[0:_HEADER_SIZE])

    def _put_header_unlocked(self, header: Header) -> None:
        assert self._mm is not None
        packed = header.pack()
        self._mm[0 : len(packed)] = packed

    def read_page(self, page_id: int) -> bytes:
        with self._mm_lock:
            assert self._mm is not None
            header = self._get_header_unlocked()
            if page_id < 0 or page_id >= header.page_count:
                raise IndexError(f"page {page_id} out of range")
            start = page_id * self._page_size
            return bytes(self._mm[start : start + self._page_size])

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) > self._page_size:
            raise ValueError(
                f"page data length {len(data)} exceeds page size {self._page_size}"
            )
        with self._mm_lock:
            assert self._mm is not None
            header = self._get_header_unlocked()
            if page_id < 0 or page_id >= header.page_count:
                raise IndexError(f"page {page_id} out of range")
            start = page_id * self._page_size
            buf = bytearray(self._page_size)
            buf[: len(data)] = data
            self._mm[start : start + self._page_size] = buf

    def alloc_page(self) -> int:
        with self._mm_lock:
            assert self._mm is not None
            header = self._get_header_unlocked()
            page_id = header.page_count
            header.page_count += 1
            new_size = header.page_count * self._page_size
            self._put_header_unlocked(header)
            if new_size > len(self._mm):
                # Grow under the lock; mapping is swapped without a None window.
                self._ensure_file_size(new_size)
                self._map(new_size)
            return page_id

    def get_header(self) -> Header:
        with self._mm_lock:
            return self._get_header_unlocked()

    def put_header(self, header: Header) -> None:
        with self._mm_lock:
            self._put_header_unlocked(header)

    def file_size(self) -> int:
        return self.get_header().page_count * self._page_size

    def close(self) -> None:
        with self._mm_lock:
            if self._mm is not None:
                try:
                    self._mm.flush()
                except (BufferError, ValueError, OSError):
                    pass
                self._mm.close()
                self._mm = None
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None
