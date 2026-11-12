package p;

interface IA { default void m() {} default void n() {} }
interface IB extends IA { default void m() {} }
class SG { void m() {} void k() {} void k(String s) {} }
class SB extends SG { void m() {} }
class SO extends SB implements IA {
    public void m() {}
    void k(Object o) {}
    class In extends SG {
        void m() {}
        void f() {
            SO.super.m();
            super.m();
            SO.this.m();
            m();
            SO.super.k();
            SO.super.k("s");
            Runnable r = new Runnable() {
                public void run() { SO.super.m(); SO.super.k("t"); }
            };
        }
    }
    void g() { IA.super.m(); IA.super.n(); SO.super.m(); }
}
class SC implements IB {
    public void m() {}
    void g() { IB.super.m(); IB.super.n(); }
}
