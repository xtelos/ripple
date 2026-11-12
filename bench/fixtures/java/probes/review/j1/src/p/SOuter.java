package p;

class SBase {
    void m() {}
}

class SOuter extends SBase {
    @Override
    void m() {}

    class Inner {
        void f() {
            SOuter.super.m();
        }
    }
}
