# Design Note: Persistent COW B+-Tree KV Store

## On-disk page format

The database file is a sequence of fixed-size pages (default 4096 bytes). Page 0 is the **header**; data pages start at id 1.

### Header (page 0, updated in place)

| Field | Type | Meaning |
|-------|------|---------|
| magic | 4 bytes | `BTRE` |
| version | u8 | format version (`1`) |
| page_size | u32 | configured page size |
| root_page_id | u32 | published tree root |
| page_count | u32 | number of pages in the file |
| freelist_head | u32 | head of free-page linked list (`0` = empty) |

### Node pages

Each tree node occupies exactly one page.

**Leaf** (`type = 1`): values live only here.

```
type:u8  count:u16  (key_len:u16  key  value_len:u16  value)*
```

**Internal** (`type = 2`): separator keys and child pointers (`len(children) = count + 1`).

```
type:u8  count:u16  (child_page_id:u32)×(count+1)  (key_len:u16  key)×count
```

Nodes are serialized with length-prefixed keys/values. A node is split when its serialized size would exceed the page size. Free pages store the next free page id as a little-endian `u32` at offset 0.

## Copy-on-write commit

`put` never mutates an existing node page in place:

1. Walk the root→leaf path (under the writer lock).
2. Copy each node on the path; apply the upsert to the new leaf.
3. On overflow, split leaf/internal nodes and propagate separators upward; height grows by allocating a new root when the old root splits.
4. Allocate replacement pages from the freelist when possible, otherwise extend the file.
5. **Publish** by a single in-place update of `root_page_id` in the header.
6. Bump the global epoch and retire the orphaned path pages.

Readers never take the writer lock. Each `get` pins the current epoch, snapshots `root_page_id` once, and walks that immutable tree version end-to-end.

## Reclamation scheme

Orphaned pages cannot be reused while any reader might still hold a snapshot that references them.

- An **epoch counter** advances on every successful commit.
- Readers **pin** the epoch observed at `get` entry and unpin on exit.
- Retired pages are held in a deferred quarantine tagged with the commit epoch `R`.
- A page becomes reusable when no reader is pinned at an epoch `<= R` (no active pins, or `min_pinned > R`).
- Reclaimed pages are pushed onto the on-disk freelist (`freelist_head` linked list). Allocation prefers the freelist before growing `page_count`.

This keeps file size bounded under repeated overwrites of a fixed key set, while preserving lock-free, snapshot-consistent reads.
