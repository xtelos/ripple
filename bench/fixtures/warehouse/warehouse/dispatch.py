"""Functions held in names and tables: functools.partial and dispatch tables."""

from functools import partial


def apply_tax(rate, amount):
    return amount + amount * rate // 100


def apply_discount(amount):
    return amount - 5


def audit(amount):
    return amount


def step_one(amount):
    return amount + 1


def step_two(amount):
    return amount * 2


def priced(amount):
    with_tax = partial(apply_tax, 10)
    return with_tax(amount)


TAXED = partial(apply_tax, 20)


def taxed(amount):
    return TAXED(amount)


# Never changed after this line, anywhere in the repo.
HANDLERS = {"discount": apply_discount, "audit": audit}
STEPS = (step_one, step_two)


def run_discount(amount):
    return HANDLERS["discount"](amount)


def run_audit(amount):
    handler = HANDLERS["audit"]
    return handler(amount)


def run_steps(amount):
    for step in STEPS:
        amount = step(amount)
    return amount


# This one is added to below, so which function a key finds is not fixed by the source.
REGISTRY = {"a": step_one}
REGISTRY["b"] = step_two


def run_registry(amount):
    return REGISTRY["a"](amount)
