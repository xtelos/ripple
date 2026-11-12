package p;

public class Qualified {
    void m() {}
    void n() {}
    class A {
        void m(int x) {}
        class B {
            void m(String s) {}
            void f() {
                Qualified.this.m();
                A.this.m(1);
                this.m("x");
                Qualified.this.n();
                A.this.toString();
            }
        }
    }
}
