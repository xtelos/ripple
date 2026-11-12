package p;

import java.util.List;

public class Bounded {
    static class Item { void add(int x) {} }
    static class Special extends Item { void add(int x) {} void extra() {} }
    static <T extends Item> void f(T t, List<? extends Item> items) {
        t.add(1);
        items.get(0).add(2);
    }
    static <T extends Special & Comparable<T>> void g(T t) {
        t.extra();
        t.add(3);
        t.compareTo(t);
    }
    <T> void h(T t) {
        t.toString();
        t.hashCode();
    }
}
