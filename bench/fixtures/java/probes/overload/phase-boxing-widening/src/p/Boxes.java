package p;

public class Boxes {
    void f(int x) {}
    void f(long x) {}
    void f(Integer x) {}
    void f(Object x) {}
    void g(long x) {}
    void g(Integer x) {}
    void h(double x) {}
    void h(Integer x) {}
    void use(Integer boxed, char c, short s, Object o) {
        f(1);
        f(1L);
        f(boxed);
        f(c);
        f(s);
        f(o);
        f("str");
        g(1);
        h(1);
        h(boxed);
    }
}
