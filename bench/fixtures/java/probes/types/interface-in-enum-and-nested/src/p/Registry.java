package p;

public class Registry {
    interface Visitor {
        void visit(Entry e);
    }
    static class Entry {
        void accept(Visitor v) { v.visit(this); }
    }
    enum Kind implements Visitor {
        ONE {
            public void visit(Entry e) { e.accept(this); }
        };
    }
    void f(Entry e, Visitor v) {
        e.accept(v);
        Kind.ONE.visit(e);
        v.visit(e);
    }
}
