package p;

public class FieldPriv {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money total = new Money();
    static class A { private Item total = new Item(); }
    class B extends A {
        void f() {
            total.add(1);
        }
    }
}
