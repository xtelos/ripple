package p;

class Item { void m() {} }
public class Order {
    void f(Item before) {
        before.m();
        class Item { void m() {} }
        Item after = new Item();
        after.m();
    }
    void g() {
        {
            class Item { void m() {} }
            new Item().m();
        }
        new Item().m();
    }
}
