package p;

public class Fields {
    static class Leaf { void go() {} }
    static class Mid { Leaf leaf = new Leaf(); static Leaf shared = new Leaf(); }
    static class Top { Mid mid = new Mid(); }
    Top top = new Top();
    static Top STATIC_TOP = new Top();
    void use(Top t) {
        t.mid.leaf.go();
        top.mid.leaf.go();
        this.top.mid.leaf.go();
        STATIC_TOP.mid.leaf.go();
        Mid.shared.go();
        Fields.STATIC_TOP.mid.leaf.go();
        p.Fields.STATIC_TOP.mid.leaf.go();
    }
}
