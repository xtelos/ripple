package p;

public class Diamond {
    interface A { default String name() { return "a"; } }
    interface B extends A { default String name() { return "b"; } }
    interface C extends A { }
    static class K implements A, B {
        public String name() { return B.super.name(); }
    }
    static class L implements C, A {
    }
    static class M implements A, B {
    }
    void use(K k, L l, M m, A a, B b) {
        k.name();
        l.name();
        m.name();
        a.name();
        b.name();
    }
}
