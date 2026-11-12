package p;

import java.util.List;
import java.util.Map;

interface Greeter {
    default String greet(String n) {
        return helper(n);
    }

    private String helper(String n) {
        return n;
    }

    static Greeter make() {
        return new Greeter() {};
    }
}

class Cart {
    void add(String s) {}

    static <T> T id(T x) {
        return x;
    }

    static void sm() {}
}

class SubCart extends Cart {
    static void sm() {}
}

class Misc {
    int add = 0;
    Map<String, List<Cart>> carts;

    void add(int x) {}

    void m() {}

    void f() {
        Greeter.make().greet("a");
        Cart.<String>id("x");
        new Cart().add("early");
        SubCart.sm();
        Cart.sm();
        add(add);
        carts.get("k").get(0).add("x");
        class Cart {
            void add(String s) {}
        }
        new Cart().add("y");
        Runnable r = new Runnable() {
            public void run() {
                m();
                Misc.this.m();
            }
        };
        var c = new SubCart();
        c.add("z");
        Object o = c;
        if (o instanceof SubCart sc && sc != null) {
            sc.add("w");
        }
    }

    class In {
        void m() {}

        void g() {
            Misc.this.m();
            m();
        }
    }
}
