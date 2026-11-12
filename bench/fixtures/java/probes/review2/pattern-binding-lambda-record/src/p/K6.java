package p;

class Money { void add(int i) {} }
class PItem { void add(int i) {} }
record Pair(Object a, Object b) {}

public class K6 {
    Money total;
    Money first;
    void f(Object o) {
        if (o instanceof PItem total) {
            Runnable r = () -> total.add(1);
        }
    }
    void g(Object o) {
        if (o instanceof Pair(PItem first, var second)) {
            first.add(2);
        }
    }
    void h(Object o) {
        switch (o) {
            case Pair(PItem first, Object b) -> first.add(3);
            default -> {}
        }
    }
    void k(Object o) {
        if (o instanceof Pair(PItem only, var b)) {
            only.add(4);
        }
    }
}
