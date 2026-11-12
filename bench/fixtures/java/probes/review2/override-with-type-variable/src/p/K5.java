package p;

class Item5 {}
interface Visitor<T> { void visit(T x); }
class V implements Visitor<Item5> {
    public void visit(Item5 x) {}
    public void visit(String s) {}
}
interface I5 { void m(); }
class A5 { public void m() {} }
class B5 extends A5 implements I5 {}

public class K5 {
    void f(I5 i) {
        Visitor<Item5> v = new V();
        v.visit(new Item5());
        i.m();
    }
}
