package p.b;

import p.a.Base;

public class Sub extends Base {
    Sub() {
        super("x");
    }
    class Inner {
        void f(Sub s) {
            tick();
            s.tick();
            Sub.this.tick();
            sTick();
            Base.sTick();
        }
    }
    void g() {
        Runnable r = new Runnable() {
            public void run() {
                tick();
            }
        };
        Base b = new Base() {
            void extra() { tick(); }
        };
        Base c = new Base("y") {};
    }
}
