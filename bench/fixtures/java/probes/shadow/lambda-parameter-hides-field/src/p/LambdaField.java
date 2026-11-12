package p;

import java.util.function.Consumer;

public class LambdaField {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money x = new Money();
    void each(Consumer<Item> c) {}
    void f() {
        x.add(1);
        each(x -> x.add(2));
        each((Item x) -> x.add(3));
        x.add(4);
    }
}
