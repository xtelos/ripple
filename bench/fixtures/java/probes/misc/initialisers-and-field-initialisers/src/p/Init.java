package p;

public class Init {
    static class Item { void add(int x) {} }
    static Item make() { return new Item(); }
    static final Item SHARED = make();
    static { SHARED.add(1); }
    final Item mine = make();
    { mine.add(2); }
    int n = compute();
    int compute() { return 1; }
    Init() {
        mine.add(3);
    }
    Init(int k) {
        this();
        mine.add(k);
    }
    interface Consts {
        Item ITEM = make2();
        static Item make2() { return new Item(); }
    }
}
