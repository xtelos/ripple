package p;

public class Inherit {
    static class Base {
        void m(String s) {}
        void n(Object o) {}
    }
    static class Sub extends Base {
        void m(Object o) {}
        void n(String s) {}
    }
    void use(Sub sub, Base base) {
        sub.m("x");
        sub.m(1);
        sub.n("x");
        sub.n(1);
        base.m("x");
        base.n("x");
        ((Base) sub).m("y");
    }
}
