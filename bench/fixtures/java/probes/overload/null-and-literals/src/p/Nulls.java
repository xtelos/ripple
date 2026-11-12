package p;

public class Nulls {
    void f(String s) {}
    void f(Object o) {}
    void g(String s) {}
    void g(StringBuilder b) {}
    void h(int i) {}
    void h(double d) {}
    void h(String s) {}
    void use() {
        f(null);
        f("x");
        f((Object) null);
        f((String) null);
        g((String) null);
        h(1);
        h(1.5);
        h(2.5f);
        h('c');
        h("s");
    }
}
