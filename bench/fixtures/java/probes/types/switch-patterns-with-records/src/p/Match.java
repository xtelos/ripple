package p;

public class Match {
    static class Item { void add(int x) {} }
    static class Money { void add(int x) {} }
    record Box(Item item, Money money) {}
    void f(Object o) {
        switch (o) {
            case Item i -> i.add(1);
            case Money m -> m.add(2);
            case Box(Item a, Money b) -> {
                a.add(3);
                b.add(4);
            }
            default -> {}
        }
        if (o instanceof Box(Item a, var b) && a != null) {
            a.add(5);
        }
        if (o instanceof Item i && o instanceof Money m) {
            i.add(6);
            m.add(7);
        }
    }
}
