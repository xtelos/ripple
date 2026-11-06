"""Calls on the result of a call: factories, constructors and chained calls."""


class Picker:
    def run(self, order):
        return f"picked {order}"


class Scanner:
    def scan(self, order):
        return f"scanned {order}"


class Dispatcher:
    def place(self, order):
        return f"placed {order}"

    def current(self) -> "Dispatcher":
        return self

    @classmethod
    def fresh(cls) -> "Dispatcher":
        return cls()


DISPATCHER = Dispatcher()


def make_picker() -> Picker:
    return Picker()


def get_service() -> Dispatcher:
    return DISPATCHER


def untyped_factory():
    return Scanner()


def pick_one(order):
    return make_picker().run(order)


def pick_two(order):
    return Picker().run(order)


def place_via_registry(order):
    return get_service().place(order)


def chained(order):
    return Dispatcher().current().place(order)


def from_classmethod(order):
    return Dispatcher.fresh().place(order)


def pick_three(order):
    return untyped_factory().scan(order)
