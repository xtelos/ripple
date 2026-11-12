package p;

public class Scope {
    void f(boolean b) {
        new Cart().add("before");
        if (b) {
            class Cart {
                void add(String s) {}
                static void sm() {}
            }
            new Cart().add("inside");
            Cart.sm();
        }
        new Cart().add("after");
        Cart.sm();
    }
}
