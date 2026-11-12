package p;

class T { void m() {} }
class Holder<T> {
    void put(T x) {}
    void put(String s, int n) {}
    class Inner {
        void use(T t) {}
        void use(p.T real) { real.m(); }
    }
}

public class Shadow {
    void h(Holder<String> h, Holder<String>.Inner in) {
        h.put("x");
        h.put("x", 1);
        in.use(new T());
    }
}
