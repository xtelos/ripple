package p;

public class Redecl {
    interface I { default int v() { return 1; } int w(); }
    static abstract class A implements I {
        public abstract int v();
    }
    static class B extends A {
        public int v() { return 2; }
        public int w() { return 3; }
    }
    static abstract class C implements I {
        public int w() { return 4; }
    }
    void use(A a, B b, C c, I i) {
        a.v();
        b.v();
        c.v();
        c.w();
        i.v();
        i.w();
    }
}
