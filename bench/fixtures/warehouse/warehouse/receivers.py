"""Helpers that call methods on objects from the standard library."""

import threading
from collections import deque
from pathlib import Path


class Journal:
    """A repo class whose method names are also used by standard library classes."""

    def __init__(self):
        self.lines = []

    def append(self, line):
        self.lines.append(line)

    def read_text(self):
        return "\n".join(self.lines)

    def wait(self):
        return len(self.lines)


class LoudDeque(deque):
    """Overrides a deque method, so a call on a value annotated `deque` may reach it."""

    def extend(self, items):
        super().extend(item.upper() for item in items)


def recent(limit):
    queue = deque(maxlen=limit)
    queue.append("x")
    queue.extend(["y"])
    return list(queue)


def trim(buf: deque):
    buf.append("z")
    buf.extend(["w"])
    return len(buf)


def load(directory):
    path = Path(directory)
    return path.read_text()


class Gate:
    def __init__(self):
        self.ready = threading.Event()

    def open(self):
        self.ready.set()
        return self.ready.wait(0.1)


def drain(sink):
    sink.append("drained")
    return sink
