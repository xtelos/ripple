package p;

public class Nest {
    void m() {}

    static class A {
        private void m(String s) {}
        public void m(Object o) {}
        private void solo() {}
    }

    static class B extends A {
        void f() {
            m("x");
            this.m("y");
            new B().m("z");
        }
    }

    static class A2 {
        private void m() {}
    }

    class Inner extends A2 {
        void f() {
            m();
        }
    }
}
