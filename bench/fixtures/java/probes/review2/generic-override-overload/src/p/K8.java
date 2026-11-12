package p;

class Base8<T> { void put(T x) {} }
class Sub8 extends Base8<String> {
    void put(String s) {}
    void put(Integer i) {}
}
class Plain8 { void m(String s) {} }
class Gen8<T> extends Plain8 { void m(T x) {} }
class Deep8 extends Gen8<Integer> {}

public class K8 {
    void f(Base8<String> b, Plain8 p, Gen8<Integer> g) {
        b.put("x");
        p.m("y");
        g.m(1);
    }
}
