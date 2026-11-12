package p;

class Base {
    Base(int a) {}
    Base(int a, int b) {}
}
class Derived extends Base {
    Derived() { this(1 /* one */); }
    Derived(int a) { super(/* first */ a); }
    Derived(int a, int b) { super(a, // second
        b); }
}
enum Level {
    LOW(1 /* one */),
    HIGH(2, /* two */ 3 /* three */);
    Level(int a) {}
    Level(int a, int b) {}
}

public class Ctors {
    void h() {
        new Derived(1 /* x */);
        new Derived(1, /* y */ 2);
        new Derived();
    }
}
