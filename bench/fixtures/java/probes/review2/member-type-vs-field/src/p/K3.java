package p;

class Other { void m() {} }
class Outer3 {
    static class Inner { static void m() {} }
    static Other Inner = new Other();
}

public class K3 {
    void f() {
        Outer3.Inner.m();
    }
}
