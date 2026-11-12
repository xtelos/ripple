package p;

public class Patterns {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money total = new Money();
    void f(Object o) {
        if (o instanceof Item total) {
            total.add(1);
        }
        total.add(2);
        if (!(o instanceof Item total)) {
            return;
        }
        total.add(3);
    }
    void g(Object o) {
        if (o instanceof Item item && item != null) {
            item.add(4);
        }
        boolean b = o instanceof Item other && other != null;
        total.add(5);
    }
}
