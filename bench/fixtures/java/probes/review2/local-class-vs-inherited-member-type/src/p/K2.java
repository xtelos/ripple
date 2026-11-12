package p;

class Base2 { static class Item { void m() {} } }

public class K2 {
    void f() {
        class Item { void m() {} }
        new Item().m();
        class L extends Base2 {
            void g(Item x) { x.m(); }
        }
        Object o = new Base2() {
            void h(Item y) { y.m(); }
        };
    }
}
