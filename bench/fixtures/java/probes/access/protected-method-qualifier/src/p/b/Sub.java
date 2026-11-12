package p.b;

import p.a.Base;

public class Sub extends Base {
    void f(Base base, Sub sub) {
        base.m("x");
        sub.m("y");
        this.m("z");
        m("w");
    }
}
