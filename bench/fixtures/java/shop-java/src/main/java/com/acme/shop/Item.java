package com.acme.shop;

public record Item(String name, int cents) {
    public Item {
        if (cents < 0) throw new IllegalArgumentException(name);
    }

    public static Item free(String name) {
        return new Item(name, 0);
    }

    public Item discounted(int pct) {
        return new Item(name, cents - cents * pct / 100);
    }

    public String shout() {
        return name().toUpperCase();
    }
}
