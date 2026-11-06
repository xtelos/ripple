"""Decorators that wrap: the wrapper is what the name is bound to, and it calls the function it wraps."""

import functools

AUDIT = []
COUNTS = {}


def audited(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        AUDIT.append(fn.__name__)
        return fn(*args, **kwargs)

    return inner


def counted(label):
    def decorate(fn):
        def counter(*args, **kwargs):
            COUNTS[label] = COUNTS.get(label, 0) + 1
            return fn(*args, **kwargs)

        return counter

    return decorate


@counted("ship")
@audited
def ship(order):
    return f"shipped {order}"


class Dock:
    @audited
    def unload(self, crate):
        return f"unloaded {crate}"


def run_ship(order):
    return ship(order)


def run_unload(dock: Dock, crate):
    return dock.unload(crate)
