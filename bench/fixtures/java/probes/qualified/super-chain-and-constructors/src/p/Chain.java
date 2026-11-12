package p;

public class Chain {
    static class A {
        A() {}
        A(String s) {}
        void m() {}
        static void sm() {}
    }
    static class B extends A {
        B() {
            super("x");
        }
        B(int n) {
            this();
        }
        void m() {
            super.m();
        }
        static void sm() {}
    }
    static class C extends B {
        C() {
            super(1);
        }
        void m() {
            super.m();
        }
    }
    void use(A a, B b) {
        a.m();
        b.m();
        a.sm();
        b.sm();
        A.sm();
        B.sm();
        C.sm();
    }
}
