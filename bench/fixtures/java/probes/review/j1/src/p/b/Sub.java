package p.b;

public class Sub extends p.a.Base {
    void f() {
        m("x");
        new Sub().m("y");
    }
}
