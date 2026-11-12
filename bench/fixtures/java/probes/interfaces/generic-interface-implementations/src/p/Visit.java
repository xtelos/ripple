package p;

public class Visit {
    interface Visitor<R> { R visit(Node n); }
    static class Node {
        <R> R accept(Visitor<R> v) { return v.visit(this); }
    }
    static class Printer implements Visitor<String> {
        public String visit(Node n) { return ""; }
    }
    void use(Node n, Printer p, Visitor<Integer> v) {
        n.accept(p);
        p.visit(n);
        v.visit(n);
        n.accept(x -> 1);
    }
}
