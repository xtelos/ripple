package p;

public class ScopedLocals {
    static class Money { void add(int x) {} }
    static class Item implements AutoCloseable {
        void add(int x) {}
        public void close() {}
    }
    Money i = new Money();
    Money e = new Money();
    Money r = new Money();
    void f(Item[] items) {
        for (Item i = new Item(); i != null; i = null) {
            i.add(1);
        }
        i.add(2);
        for (Item e : items) {
            e.add(3);
        }
        e.add(4);
        try (Item r = new Item()) {
            r.add(5);
        }
        r.add(6);
        try {
            r.add(7);
        } catch (RuntimeException e) {
            e.getMessage();
        }
        e.add(8);
    }
}
