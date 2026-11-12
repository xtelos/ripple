package p;

public class Hide {
    static class A {
        private void m() {}
        void call() {
            m();
        }
    }
    static class B extends A {
        void m() {}
    }
    void use(A a, B b) {
        a.call();
        b.call();
        b.m();
    }
}
