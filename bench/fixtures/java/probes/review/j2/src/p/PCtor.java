package p;

class PCtor {
    private PCtor(String s) {}
    PCtor(Object o) {}
}

class UsePCtor {
    Object f() {
        new p.a.Ctor("x");
        return new PCtor("y");
    }
}
