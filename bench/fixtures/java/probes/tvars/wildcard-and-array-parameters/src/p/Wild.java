package p;

import java.util.List;

class Item {}
class Sink {
    void m(List<?> xs) {}
    void m(String s) {}
    void m(Item[] items) {}
    <T> void m(T[] xs, int n) {}
    void m(String[] xs, int n) {}
    void n(List<? extends Item> xs) {}
    void n(Item i) {}
}

public class Wild {
    void h(Sink s, List<Item> items, Item[] arr, String[] strs) {
        s.m(items);
        s.m("x");
        s.m(arr);
        s.m(strs, 1);
        s.m(arr, 2);
        s.n(items);
        s.n(new Item());
    }
}
