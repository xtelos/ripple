package p.b;

import p.a.Base;

public class Sub extends Base {
    void m() {}
    void use(Base base) {
        base.run();
        m();
    }
}
