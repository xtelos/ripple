package p;

public class Outer {
    void m(String s) {}
    void only() {}
    class Inner {
        void m(int x) {}
        void f() {
            m(1);
            only();
            Outer.this.m("x");
            Outer.this.only();
        }
    }
    static class Nested {
        void m(Object o) {}
        void g(Outer outer) {
            m("y");
            outer.m("z");
            outer.only();
        }
    }
}
