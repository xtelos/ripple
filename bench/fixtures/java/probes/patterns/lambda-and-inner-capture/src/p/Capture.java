package p;

import java.util.function.Supplier;

class PItem { void add(int n) {} }
class Money { void add(int n) {} }

public class Capture {
    Money total = new Money();
    void lambda(Object o) {
        if (o instanceof PItem total) {
            Runnable r = () -> total.add(1);
            r.run();
        }
    }
    void anonymous(Object o) {
        if (o instanceof PItem total) {
            Runnable r = new Runnable() {
                public void run() { total.add(2); }
            };
            r.run();
        }
    }
    void local(Object o) {
        if (o instanceof PItem total) {
            class L { void go() { total.add(3); } }
            new L().go();
        }
    }
    void noField(Object o) {
        if (o instanceof PItem only) {
            Supplier<Integer> s = () -> { only.add(4); return 1; };
            s.get();
        }
    }
    void outside() {
        total.add(5);
    }
}
