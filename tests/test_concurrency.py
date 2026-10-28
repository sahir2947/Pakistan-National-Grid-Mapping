"""One Grid shared by many threads, or opened before a fork, as a web server uses it."""

import json
import os
import random
import sqlite3
import threading

import pytest

from pakgrid import Grid


def _points(seed, n):
    rng = random.Random(seed)
    return [(rng.uniform(31.48, 31.57), rng.uniform(74.0, 74.6), rng.choice([None, 5, 30])) for _ in range(n)]


def _answers(grid, points):
    return [loc.to_dict() for loc in grid.locate_many(points)]


def test_threads_share_one_grid(lahore_db):
    points = _points(1, 400)
    with Grid.open(lahore_db) as grid:
        expected = _answers(grid, points)
        results, errors, used = {}, [], {}
        start = threading.Barrier(8)

        def work(k):
            try:
                start.wait()
                for _ in range(3):
                    results[k] = _answers(grid, points)
                    grid.neighbours(grid.locate(31.52, 74.36).cell_id)
                used[k] = grid.store._db
            except Exception as exc:       # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert all(results[k] == expected for k in range(8))
        conns = {id(c) for c in used.values()} | {id(grid.store._db)}
        assert len(conns) == 9                 # each thread used its own connection


def test_connections_of_finished_threads_are_released(lahore_db):
    with Grid.open(lahore_db) as grid:
        def work():
            grid.locate(31.52, 74.36)
        for _ in range(20):
            t = threading.Thread(target=work)
            t.start()
            t.join()
        assert len(grid.store._open) < 20


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_grid_opened_before_fork(lahore_db):
    points = _points(2, 200)
    with Grid.open(lahore_db) as grid:
        expected = _answers(grid, points)       # the parent has used its connection
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:                            # child: like a gunicorn worker
            try:
                os.close(read)
                payload = json.dumps(_answers(grid, points)).encode()
            except BaseException as exc:
                payload = json.dumps({"error": repr(exc)}).encode()
            with os.fdopen(write, "wb") as f:
                f.write(payload)
            os._exit(0)
        os.close(write)
        with os.fdopen(read, "rb") as f:
            got = json.loads(f.read())
        os.waitpid(pid, 0)
        assert got == json.loads(json.dumps(expected))
        assert _answers(grid, points) == expected   # the parent's connection still works


def test_closed_grid_cannot_be_used(lahore_db):
    grid = Grid.open(lahore_db)
    other = []
    t = threading.Thread(target=lambda: other.append(grid.locate(31.52, 74.36)))
    t.start()
    t.join()
    grid.close()
    with pytest.raises(sqlite3.ProgrammingError):
        grid.locate(31.52, 74.36)
    t = threading.Thread(target=lambda: other.append(pytest.raises(sqlite3.ProgrammingError, grid.locate, 31.52, 74.36)))
    t.start()
    t.join()
    assert len(other) == 2
