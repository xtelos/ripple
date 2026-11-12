package p.b;

import p.a.Pub;

public class Use {
    void f() {
        new Pub().m("x");
        Pub.make().m("y");
        new Pub().q("z");
        Object o = "w";
        new Pub().q(o);
    }
}
