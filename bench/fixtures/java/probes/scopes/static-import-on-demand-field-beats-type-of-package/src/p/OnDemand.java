package p;

import static p.Consts.*;

class Item { static void m() {} }
class Other { static void m() {} }
class Consts {
    static Other Item = new Other();
}
public class OnDemand {
    void h() {
        Item.m();
    }
}
