package p;

public class Objs {
    interface Shape { double area(); }
    static class Circle implements Shape {
        public double area() { return 1; }
        public String toString() { return "circle"; }
        public boolean equals(Object o) { return true; }
        public int hashCode() { return 1; }
    }
    static class Plain {}
    static class Fancy extends Plain {
        public String toString() { return "fancy"; }
    }
    enum Kind {
        A { public String toString() { return "a"; } },
        B
    }
    void use(Shape s, Plain p, Fancy f, Kind k, Circle c) {
        s.toString();
        s.hashCode();
        s.equals(s);
        p.toString();
        p.hashCode();
        f.toString();
        k.toString();
        k.name();
        c.toString();
        s.getClass();
    }
}
