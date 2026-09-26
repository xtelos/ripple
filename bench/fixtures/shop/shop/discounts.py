"""Discount codes. Subclasses decide how much to take off."""


class Discount:
    def __init__(self, code):
        self.code = code

    def apply(self, amount):
        return amount - self.amount_off(amount)

    def amount_off(self, amount):
        raise NotImplementedError


class PercentOff(Discount):
    def __init__(self, code, percent):
        super().__init__(code)
        self.percent = percent

    def amount_off(self, amount):
        return amount * self.percent // 100


class FixedOff(Discount):
    def __init__(self, code, cents):
        super().__init__(code)
        self.cents = cents

    def amount_off(self, amount):
        return min(self.cents, amount)


def best_discount(discounts, amount):
    """Pick the discount that saves the customer the most."""
    if not discounts:
        return None
    return max(discounts, key=lambda d: d.amount_off(amount))
