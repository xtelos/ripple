package com.acme.shop;

import java.util.List;

public class Chains {
    private final Cart cart = new Cart();

    Cart cart() {
        return cart;
    }

    String chained() {
        return cart().copy().receipt();
    }

    void typed() {
        var c = new Cart();
        c.add("v");
        Item item = Item.free("i");
        c.add(item);
    }

    void casts(Object o) {
        ((Cart) o).copy();
        if (o instanceof Item i) {
            i.name();
        }
    }

    String switched(Object o) {
        return switch (o) {
            case Item i -> i.shout();
            case Cart c -> c.receipt();
            default -> "";
        };
    }

    void generics(List<Item> items, Box<Cart> box) {
        items.get(0).name();
        box.get().receipt();
        box.get();
    }

    int parse() {
        return Integer.parseInt("1");
    }

    void unknown(Cart[] many, boolean b, Cart x, Cart y) {
        many[0].total();
        (b ? x : y).total();
        Object o = x;
        o.hashCode();
    }

    void reflect(Object target) throws Exception {
        target.getClass().getMethod("total").invoke(target);
        AutoValue_Item.create("x");
    }

    static Item create(String s) {
        return Item.free(s);
    }
}

class Box<T> {
    private final T value;

    Box(T value) {
        this.value = value;
    }

    T get() {
        return value;
    }
}
