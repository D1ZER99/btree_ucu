"""Free-page list with epoch-gated reclamation."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Tuple

from .epoch import EpochTracker
from .page_store import PageStore

# Free pages store next page id at offset 0 (u32). 0 means end of list.
_NEXT = struct.Struct("<I")


@dataclass
class _Deferred:
    retire_epoch: int
    page_ids: List[int]


class FreeList:
    """
    Allocate from the on-disk freelist before extending the file.
    Retired (COW-orphaned) pages are quarantined until no reader can
    still observe them, then pushed onto the freelist.
    """

    def __init__(self, store: PageStore, epochs: EpochTracker) -> None:
        self._store = store
        self._epochs = epochs
        self._deferred: List[_Deferred] = []

    def allocate(self) -> int:
        self.reclaim()
        header = self._store.get_header()
        if header.freelist_head != 0:
            page_id = header.freelist_head
            page = self._store.read_page(page_id)
            (nxt,) = _NEXT.unpack_from(page)
            header.freelist_head = nxt
            self._store.put_header(header)
            return page_id
        return self._store.alloc_page()

    def retire(self, page_ids: List[int], retire_epoch: int) -> None:
        if not page_ids:
            return
        self._deferred.append(_Deferred(retire_epoch, list(page_ids)))
        self.reclaim()

    def reclaim(self) -> None:
        if not self._deferred:
            return
        still: List[_Deferred] = []
        freed: List[int] = []
        for item in self._deferred:
            if self._epochs.is_safe_to_reclaim(item.retire_epoch):
                freed.extend(item.page_ids)
            else:
                still.append(item)
        self._deferred = still
        for page_id in freed:
            self._push_free(page_id)

    def _push_free(self, page_id: int) -> None:
        header = self._store.get_header()
        page = bytearray(self._store.page_size())
        _NEXT.pack_into(page, 0, header.freelist_head)
        self._store.write_page(page_id, bytes(page))
        header.freelist_head = page_id
        self._store.put_header(header)

    def deferred_count(self) -> int:
        return sum(len(d.page_ids) for d in self._deferred)
