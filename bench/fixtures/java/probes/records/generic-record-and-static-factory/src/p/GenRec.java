package p;

class Item { void m() {} }
record Box<T>(T value) {
    static <T> Box<T> of(T v) { return new Box<>(v); }
    Box<T> self() { return this; }
}
record Named(String name, Item item) {
    static Named of(String n) { return new Named(n, new Item()); }
    public Item item() { return item; }
}

public class GenRec {
    void h(Box<Item> b) {
        Box.of(new Item());
        b.self().self();
        b.value();
        Named.of("x").item().m();
    }
}
