package p;

interface Cmp<T extends Number> { void take(T t); }
class C implements Cmp<Integer> {
    public void take(Integer i) {}
    public void take(String s) {}
}
interface Conv<A, B> { B apply(A a); }
class Len implements Conv<String, Integer> {
    public Integer apply(String s) { return s.length(); }
    public Integer apply(Integer i) { return i; }
}

public class Erasure {
    void h(Cmp<Integer> c, Conv<String, Integer> v) {
        c.take(1);
        v.apply("s");
        new C().take("x");
        new Len().apply(3);
    }
}
