package p.b;

import p.a.Base;

public class Holder {
    static class Money { void add(int x) {} }
    Money tag = new Money();
    class Sub extends Base {
        void f() {
            tag.add(1);
        }
    }
}
