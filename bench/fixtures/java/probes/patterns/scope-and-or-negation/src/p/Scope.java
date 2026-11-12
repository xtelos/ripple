package p;

class A { boolean ok() { return true; } void use() {} }
class Fa { boolean ok() { return false; } void use() {} }

public class Scope {
    Fa a = new Fa();
    void andScope(Object o) {
        if (o instanceof A a && a.ok()) {
            a.use();
        }
        a.use();
    }
    void orScope(Object o) {
        if (!(o instanceof A a) || a.ok()) {
            a.getClass();
        }
        if (!(o instanceof A a)) {
            return;
        }
        a.use();
    }
    void whileScope(Object o) {
        while (!(o instanceof A a)) {
            o = new A();
        }
        a.use();
    }
    boolean ternary(Object o) {
        return o instanceof A a ? a.ok() : false;
    }
}
