package p;

class Other { void m() {} static void s() {} }
interface Api {
    class Conf { static void s() {} }
}
interface Holder {
    Other Conf = new Other();
}
class Both implements Holder {
    void f() { Conf.s(); }
}
class OnlyApi implements Api {
    void f() { Conf.s(); }
}
public class Consts {}
