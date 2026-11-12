package com.acme.shop;

import java.util.HashMap;
import java.util.Map;

interface Store {
    Item find(String name);

    void put(Item item);
}

class MemoryStore implements Store {
    private final Map<String, Item> items = new HashMap<>();

    public Item find(String name) {
        return items.get(name);
    }

    public void put(Item item) {
        items.put(item.name(), item);
    }
}

class FileStore implements Store {
    public Item find(String name) {
        return Item.free(name);
    }

    public void put(Item item) {}
}

@interface Inject {}

/** A field the container fills: its declared type bounds what may be there. */
public class Inventory {
    @Inject
    private Store store;

    Item get(String name) {
        return store.find(name);
    }

    void add(Item item) {
        store.put(item);
    }

    void reload() {
        new MemoryStore().put(Item.free("x"));
    }
}
