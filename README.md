# Persistent COW B+-Tree KV Store

Python key-value store backed by a copy-on-write B+-tree over fixed-size pages. Data can live in memory or in a memory-mapped file. Reads use snapshot isolation (lock-free for writers); writes are single-writer with path copying and epoch-gated page reuse.

## Requirements

- Python 3.10+
- Stdlib only for the core; `pytest` for tests

## Install & test

```bash
pip install -e ".[dev]"
pytest -q
```

## Quick start

```python
from btree import KVStore

# Persistent (mmap file)
with KVStore("data.db", page_size=4096) as db:
    db.put(b"key", b"value")
    assert db.get(b"key") == b"value"
    assert db.get(b"missing") is None

# In-memory (no file)
db = KVStore()
db.put(b"a", b"1")
db.close()
```

Public API: `put(key, value)`, `get(key) → bytes | None`, `close()`. Keys/values are `bytes`; `put` upserts; there is no delete.

## Project layout

| Path | Role |
|------|------|
| `btree/` | Core: page store, nodes, COW tree, freelist/epochs, `KVStore` |
| `tests/` | Unit, mmap persistence, concurrency / space-reuse |
| `DESIGN.md` | On-disk format, COW commit, reclamation |
| `TESTING.md` | Manual smoke checks and how to run automated tests |

## More detail

Full design (page layout, COW publish, freelist/epochs) is in **[DESIGN.md](DESIGN.md)**. Setup, pytest, and manual interactive checks are in **[TESTING.md](TESTING.md)**.
