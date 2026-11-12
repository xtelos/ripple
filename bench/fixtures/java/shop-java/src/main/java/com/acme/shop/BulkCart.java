package com.acme.shop;

public class BulkCart extends Cart {
    private final int factor;

    public BulkCart(int factor) {
        super();
        this.factor = factor;
    }

    // an overload of Cart.add(Item) and Cart.add(String) with other parameters: not an override
    public void add(Item item, int times) {
        for (int i = 0; i < times; i++) {
            super.add(item);
        }
    }

    @Override
    public int total() {
        return super.total() * factor;
    }

    public void fill() {
        add("bulk");
        add(Item.free("x"), 2);
        this.add(Item.free("y"));
    }
}
