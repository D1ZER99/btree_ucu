"""Concurrency and space-reuse tests."""

from __future__ import annotations

import threading
import time

from btree import KVStore


def test_concurrent_readers_during_writes(tmp_path):
    path = str(tmp_path / "conc.db")
    db = KVStore(path, page_size=512)

    # Seed stable keys.
    for i in range(50):
        db.put(f"s{i:03d}".encode(), f"seed{i}".encode())

    stop = threading.Event()
    errors: list[str] = []

    def writer() -> None:
        n = 0
        while not stop.is_set():
            key = f"w{n % 30:03d}".encode()
            db.put(key, f"val-{n}".encode())
            n += 1
            if n > 500:
                break

    def reader(rid: int) -> None:
        while not stop.is_set():
            for i in range(50):
                got = db.get(f"s{i:03d}".encode())
                if got != f"seed{i}".encode():
                    errors.append(
                        f"reader{rid}: seed s{i:03d} got {got!r}"
                    )
                    return
            # Also read writer keys — value may be any complete val-N or missing.
            for i in range(30):
                got = db.get(f"w{i:03d}".encode())
                if got is not None and not got.startswith(b"val-"):
                    errors.append(f"reader{rid}: torn value {got!r}")
                    return

    threads = [threading.Thread(target=writer)]
    for r in range(8):
        threads.append(threading.Thread(target=reader, args=(r,)))

    for t in threads:
        t.start()
    time.sleep(0.05)
    # Let writer finish naturally; then stop readers.
    threads[0].join(timeout=30)
    stop.set()
    for t in threads[1:]:
        t.join(timeout=10)

    assert not errors, errors
    db.close()


def test_file_size_bounded_under_overwrite(tmp_path):
    path = str(tmp_path / "reuse.db")
    db = KVStore(path, page_size=512)

    keys = [f"k{i:02d}".encode() for i in range(20)]
    for k in keys:
        db.put(k, b"initial")

    # Warm up tree structure.
    for round_i in range(50):
        for k in keys:
            db.put(k, f"r{round_i}".encode() * 4)

    size_after_warmup = db.file_size()

    for round_i in range(200):
        for k in keys:
            db.put(k, f"r{round_i}".encode() * 4)

    size_final = db.file_size()
    # Without reuse, 200 rounds of path-copy would grow unboundedly.
    # Allow modest slack for deferred quarantine pages.
    assert size_final <= size_after_warmup * 3, (
        f"file grew too much: warmup={size_after_warmup} final={size_final}"
    )
    assert size_final < 512 * 500

    for k in keys:
        assert db.get(k) is not None
    db.close()


def test_in_memory_concurrent_reads():
    db = KVStore(page_size=512)
    for i in range(100):
        db.put(f"k{i:03d}".encode(), b"v")

    errors: list[str] = []
    barrier = threading.Barrier(10)

    def reader() -> None:
        barrier.wait()
        for _ in range(100):
            for i in range(100):
                if db.get(f"k{i:03d}".encode()) != b"v":
                    errors.append("bad read")
                    return

    threads = [threading.Thread(target=reader) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    db.close()
