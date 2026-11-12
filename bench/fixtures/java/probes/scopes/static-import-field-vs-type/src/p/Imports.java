package p;

import static p.Util.Cart;
import static p.Util.*;

class Other { void m() {} static void s() {} }
class Cart { static void m() {} }
class Cell { static void m() {} }
class Util {
    static Other Cart = new Other();
    static Other Cell = new Other();
}
public class Imports {
    void h() {
        Cart.m();
        Cell.m();
    }
}
