package p;

class Inner { static void m() {} }
class Other { static void m() {} }
class Outer {
    static class Inner { static void m() {} }
    static Other Inner;
    static class OnlyType { static void m() {} }
    static Other Mixed;
    static class Mixed2 { static void m() {} }
}
class Both {
    static class Both2 { static void m() {} }
    static Other Both2;
    static void call() { Both2.m(); }
}
public class QualField {
    void h() {
        Outer.Inner.m();
        Outer.OnlyType.m();
        Outer.Mixed.m();
        Outer.Mixed2.m();
        Both.Both2.m();
    }
}
