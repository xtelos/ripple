package p;

interface Op13 { int apply(int x); }
enum Ops implements Op13 {
    INC { public int apply(int x) { return helper(x) + 1; } },
    DEC { public int apply(int x) { return x - 1; } };
    private static int helper(int x) { return x; }
    int twice(int x) { return apply(apply(x)); }
}
interface Consts13 { Item13 X = new Item13(); }
class Item13 { void m() {} void m(Object o) {} void m(String s) {} void v(String... s) {} void v(Object o) {} }
class Base13 { static class Inner { void z() {} } static void s() {} Item13 f = new Item13(); }
class Sub13 extends Base13 { Other13 f = new Other13(); }
class Other13 { void m() {} }
interface L1 { void k(String s); }
interface L2 { void k(Object o); }
abstract class Both implements L1, L2 {}

public class K13 implements Consts13 {
    void f(Op13 op, Ops e, Sub13 sub, Both b, Item13 it, boolean c) {
        op.apply(1);
        e.apply(2);
        e.ordinal();
        Ops.valueOf("INC").twice(3);
        Ops.values();
        switch (e) { case INC -> it.m(); default -> it.m("s"); }
        X.m();
        Consts13.X.m();
        new Sub13.Inner().z();
        Sub13.s();
        sub.f.m();
        ((Base13) sub).f.m();
        b.k("s");
        it.v();
        it.v("a");
        it.v((Object) "a");
        it.m("a" + 1);
        it.m(c ? "x" : "y");
        it.m((String) null);
        it.m(1 + "a");
        it.m(new String[0]);
        it.m(this);
    }
}
