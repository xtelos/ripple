package p;

import java.util.List;

class Item14 {}
class Sink14 {
    void put(Object o) {}
    void put(Item14 i) {}
    void put(String s) {}
    void num(int i) {}
    void num(long l) {}
    void num(Integer i) {}
    void num(double d) {}
}
class Mid14 extends Sink14 { void put(Item14 i) {} }
class Low14 extends Mid14 {}

public class K14 {
    static <T> T id(T x) { return x; }
    static Item14 make() { return new Item14(); }
    Integer boxed() { return 1; }
    void f(Sink14 s, Low14 low, short sh, byte by, char ch) {
        s.put(id("x"));
        s.put(id(new Item14()));
        s.put(make());
        var v = id(new Item14());
        s.put(v);
        s.put(List.of(new Item14()));
        s.num(1);
        s.num(1L);
        s.num(1.0);
        s.num(1.0f);
        s.num(sh);
        s.num(by);
        s.num(ch);
        s.num('c');
        s.num(boxed());
        s.num((int) 2L);
        low.put(new Item14());
        low.put("x");
        Sink14 viaMid = new Mid14();
        viaMid.put(new Item14());
    }
}
