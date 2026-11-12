package p;

import java.util.Optional;

public class Chains {
    static class Item { void add(int x) {} }
    static class Box<T> {
        T value;
        T get() { return value; }
        Box<T> self() { return this; }
        Item item() { return new Item(); }
    }
    static Optional<Item> find() { return Optional.empty(); }
    void use(Box<Item> box) {
        box.get().add(1);
        box.self().get().add(2);
        box.item().add(3);
        box.self().item().add(4);
        find().get().add(5);
        box.value.add(6);
        new Box<Item>().get().add(7);
    }
}
