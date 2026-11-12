package p;

class Box<T extends Comparable<T>> {
    void put(T x) {}
    void put(String s, int n) {}
    void put(Integer i) {}
}
class Plain<T> {
    void put(T x) {}
    void put(String s) {}
}

public class Bound {
    void h(Box<String> b, Plain<Integer> p) {
        b.put("x");
        b.put("x", 1);
        b.put(3);
        p.put("s");
        p.put(4);
    }
}
