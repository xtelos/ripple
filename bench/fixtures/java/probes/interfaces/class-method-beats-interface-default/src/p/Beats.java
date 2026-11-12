package p;

public class Beats {
    interface I { default int v() { return 1; } }
    interface J extends I { default int v() { return 2; } }
    static class A { public int v() { return 3; } }
    static class B extends A implements J { }
    void use(B b, J j) {
        b.v();
        j.v();
    }
}
