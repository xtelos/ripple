package p;

class Base {
    static class Item { void m() {} }
    interface Shape { void draw(); }
}
class Deep extends Base {
    void f() {
        class Item { void m() {} }
        class L extends Base {
            void g(Item x) { x.m(); }
            void h(Shape s) { s.draw(); }
        }
        new Base() {
            void k(Item y) { y.m(); }
        };
        Item own = new Item();
        own.m();
    }
}
class Outer {
    void f() {
        class Item { void m() {} }
        class Mid {
            void g() {
                class Inner extends Base {
                    void h(Item z) { z.m(); }
                }
                Item w = new Item();
                w.m();
            }
        }
        Runnable r = new Runnable() {
            public void run() {
                class Item2 extends Base { void q(Item a) { a.m(); } }
            }
        };
    }
}

public class Depths {}
