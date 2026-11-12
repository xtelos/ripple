package p;

public class SOuter extends SBase {
    void m() {}
    class Inner {
        void f() {
            SOuter.super.m();
            SOuter.this.m();
            m();
        }
    }
    void g() {
        super.m();
        this.m();
    }
}
