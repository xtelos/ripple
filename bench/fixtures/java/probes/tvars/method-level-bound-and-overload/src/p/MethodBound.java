package p;

class Item { void m() {} }
class Special extends Item {}

public class MethodBound {
    <T extends Item> void add(T x) {}
    void add(String s) {}
    void add(Special s) {}
    <T extends Comparable<T>> T max(T a, T b) { return a; }
    int max(int a, int b) { return a; }
    static <T> void w(T x) {}
    static void w(String s) {}
    void h(Item i, Special sp) {
        add(i);
        add(sp);
        add("s");
        max(1, 2);
        max("a", "b");
        w("a");
        w(1);
    }
}
