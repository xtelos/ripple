package p.a;

public class Use {
    void h(A a) {
        a.m();
        a.pm();
        new p.b.B().run(a);
        A b = new p.b.B();
        b.m();
    }
}
