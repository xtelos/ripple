package p;

class A { void use() {} }
class B { void use() {} }

public class TwoTypes {
    void h(Object o) {
        if (o instanceof A x) {
            x.use();
        } else if (o instanceof B x) {
            x.use();
        }
        if (o instanceof A y && y != null) y.use();
        if (o instanceof B y && y != null) y.use();
    }
}
