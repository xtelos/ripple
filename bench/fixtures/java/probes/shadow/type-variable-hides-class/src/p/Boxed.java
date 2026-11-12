package p;

public class Boxed<Item> {
    void f(Item i, p.Item real) {
        real.add(1);
    }
    <Cart extends p.Item> void g(Cart c) {
        c.add(1);
    }
}
