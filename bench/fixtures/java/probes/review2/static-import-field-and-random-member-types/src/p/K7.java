package p;

import static p.Util7.Cart;

class Other7 { void m() {} }
class Cart { static void m() {} }
class Util7 { static Other7 Cart = new Other7(); }
class StreamableGenerator { void ints() {} }
class MyRand extends java.util.Random {
    void g(StreamableGenerator s) { s.ints(); }
}

public class K7 {
    void f() {
        Cart.m();
    }
}
