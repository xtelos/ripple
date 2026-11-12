package p;

class A { void use() {} }
class B { void use() {} }

public class Params {
    B f = new B();
    void h(Object o, B p) {
        if (o instanceof A f) {
            f.use();
        }
        f.use();
        if (!(o instanceof A p2)) return;
        p2.use();
        p.use();
    }
    void g(Object o) {
        if (o instanceof A f && f.hashCode() > 0) {
            f.use();
        } else {
            f.use();
        }
    }
}
