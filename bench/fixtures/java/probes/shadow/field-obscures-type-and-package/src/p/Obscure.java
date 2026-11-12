package p;

public class Obscure {
    static class Money { void add(int x) {} }
    static class Cart { static void add(int x) {} }
    static class Holder { Money Helper = new Money(); }
    Money Cart = new Money();
    Holder u = new Holder();
    void f() {
        Cart.add(1);
        u.Helper.add(2);
    }
}
