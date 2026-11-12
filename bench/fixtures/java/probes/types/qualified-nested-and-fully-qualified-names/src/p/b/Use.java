package p.b;

import p.a.Outer;
import p.a.Outer.Inner;

public class Use {
    void f(Outer.Inner x, Inner y, p.a.Outer.Inner z) {
        Outer.Inner.sm();
        Inner.sm();
        p.a.Outer.Inner.sm();
        x.im();
        y.im();
        z.im();
        new Outer.Inner().im();
        new p.a.Outer.Inner().im();
    }
}
