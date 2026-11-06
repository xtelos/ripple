"""Calls written where a definition is made: annotations, defaults, decorator arguments.

Python runs these when the def or class statement runs, in the scope that holds it,
not when the function is called. Pydantic's Field(gt=0) inside Annotated[...] is the
same thing.
"""

from typing import Annotated


class Rule:
    def __init__(self, gt=0):
        self.gt = gt


def stamp(label):
    return label


def tag(label):
    def decorate(fn):
        return fn

    return decorate


class Line:
    qty: Annotated[int, Rule(gt=0)]
    sku: str = stamp("none")
    note: Annotated[str, Rule(gt=1)] = ""

    def total(self, price: Annotated[int, Rule(gt=2)] = 0) -> Annotated[int, Rule(gt=3)]:
        return price


def priced(amount: Annotated[int, Rule(gt=4)], label=stamp("t")) -> Annotated[int, Rule(gt=5)]:
    return amount


@tag(stamp("decorated"))
def tagged():
    return 1


def local_only():
    value: Annotated[int, Rule(gt=6)] = 1  # a local's annotation is never evaluated
    return value
