package p;

public class Below {
    interface I { default int v() { return 1; } }
    static class A implements I { }
    static class B extends A { public int v() { return 2; } }
    static class C extends B implements I { }
    void use(I i, A a, B b, C c) {
        i.v();
        a.v();
        b.v();
        c.v();
    }
}
