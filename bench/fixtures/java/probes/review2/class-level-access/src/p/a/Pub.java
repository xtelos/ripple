package p.a;

class Hidden { public void m(String s) {} public void q(Object o) {} }

public class Pub extends Hidden {
    public void m(Object o) {}
    public void q(String s) {}
    public static Pub make() { return new Pub(); }
}
