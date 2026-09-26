"""Order data model. Prices are integer cents to avoid float rounding."""

from dataclasses import dataclass, field


@dataclass
class LineItem:
    sku: str
    unit_price: int
    quantity: int

    def subtotal(self) -> int:
        return self.unit_price * self.quantity


@dataclass
class Order:
    customer_id: str
    items: list = field(default_factory=list)

    def add_item(self, sku, unit_price, quantity=1):
        item = LineItem(sku, unit_price, quantity)
        self.items.append(item)
        return item

    def gross_total(self) -> int:
        return sum(item.subtotal() for item in self.items)
