package p.b;

import p.a.Ctor;

public class Make extends Ctor {
    Make() {
        super("x");
    }
    static Ctor one() {
        return new Ctor("y");
    }
}
