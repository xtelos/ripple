package p;

public class Derived extends Base {
    void m(String s) {}
    class Inner {
        void f() {
            Derived.super.m("x");
            Derived.this.m("y");
            Derived.super.m(1);
        }
    }
    void g() {
        super.m("z");
        m("w");
    }
}
