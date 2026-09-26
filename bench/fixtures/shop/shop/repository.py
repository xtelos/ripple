"""In-memory order store. A real service would put a database here."""


class OrderRepository:
    def __init__(self):
        self._orders = {}
        self._next_id = 1

    def save(self, order):
        order_id = self._allocate_id()
        self._orders[order_id] = order
        return order_id

    def get(self, order_id):
        return self._orders[order_id]

    def _allocate_id(self):
        order_id = self._next_id
        self._next_id += 1
        return order_id
