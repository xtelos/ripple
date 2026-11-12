package p;

public class Locals {
    class Inner {
        Inner(String s) {}
        void go() {}
    }
    static class Nested {
        Nested(int n) {}
        void go() {}
    }
    void f(Locals other) {
        Inner a = new Inner("a");
        Inner b = other.new Inner("b");
        Inner c = this.new Inner("c");
        a.go();
        b.go();
        class Local {
            Local(long l) {}
            void go() {}
        }
        Local l = new Local(1L);
        l.go();
        Nested n = new Nested(1);
        n.go();
    }
}
