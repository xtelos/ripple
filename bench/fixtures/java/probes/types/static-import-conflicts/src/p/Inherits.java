package p;

import static p.Util.max;

public class Inherits {
    static class Base {
        void max(int a, int b, int c) {}
    }
    static class Sub extends Base {
        void f() {
            max(1, 2, 3);
        }
    }
    void g() {
        max("a", "b");
    }
}
