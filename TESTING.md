# Manual testing guide

Automated tests (`pytest`) cover the assignment requirements. Use the steps below for quick manual smoke checks.

## Setup

```bash
cd c:\BTree
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Interactive smoke checks

Start a Python REPL from the project root:

```bash
python
```

### 1. Basic put / get

```python
from btree import KVStore

db = KVStore("manual.db")
db.put(b"hello", b"world")
assert db.get(b"hello") == b"world"
assert db.get(b"missing") is None
db.close()
```

### 2. Durability (reopen)

```python
from btree import KVStore

db = KVStore("manual.db")
assert db.get(b"hello") == b"world"
db.put(b"hello", b"updated")
db.close()

db = KVStore("manual.db")
assert db.get(b"hello") == b"updated"
db.close()
```

### 3. Upsert and many keys (splits)

```python
from btree import KVStore

db = KVStore("manual_split.db", page_size=256)
for i in range(100):
    db.put(f"k{i:04d}".encode(), b"x" * 8)
for i in range(100):
    assert db.get(f"k{i:04d}".encode()) == b"x" * 8
db.close()
```

### 4. Space reuse (file size stays bounded)

```python
import os
from btree import KVStore

path = "manual_reuse.db"
if os.path.exists(path):
    os.remove(path)

db = KVStore(path, page_size=512)
keys = [f"k{i:02d}".encode() for i in range(20)]
for k in keys:
    db.put(k, b"init")

for r in range(100):
    for k in keys:
        db.put(k, f"r{r}".encode() * 4)

size = db.file_size()
print("file_size=", size)
# Expect a small multiple of page_size (typically well under 200 pages).
assert size < 512 * 200
db.close()
```

### 5. Concurrent readers (light manual check)

```python
import threading
from btree import KVStore

db = KVStore("manual_conc.db", page_size=512)
for i in range(30):
    db.put(f"s{i:02d}".encode(), b"seed")

stop = False
errors = []

def writer():
    n = 0
    while not stop and n < 300:
        db.put(b"w", f"v{n}".encode())
        n += 1

def reader():
    while not stop:
        for i in range(30):
            if db.get(f"s{i:02d}".encode()) != b"seed":
                errors.append("bad seed read")
                return

t_w = threading.Thread(target=writer)
t_r = threading.Thread(target=reader)
t_w.start(); t_r.start()
t_w.join()
stop = True
t_r.join()
assert not errors
db.close()
print("ok")
```

## What is hard to verify manually

Prefer `pytest` for:

- Asserting multi-level internal splits systematically
- Stressing many reader threads for torn reads
- Tight file-size bounds under long overwrite loops

```bash
python -m pytest tests/test_concurrency.py -q
```
