package p;

public class StaticInst {
    static void f(String s) {}
    void f(Object o) {}
    static void g(int i) {}
    void g(long l) {}
    void use(StaticInst other) {
        f("x");
        f(1);
        g(1);
        g(1L);
        other.f("y");
        StaticInst.f("z");
        other.g(2);
    }
}
