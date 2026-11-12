package p;

record P(int x, int y) {
    P(String a, String b) { this(1, 2); }
}
record Q(String s) {
    Q { s.length(); }
}
record R(int v) {
    R(long w) { this((int) w); }
}

public class K4 {
    void f() {
        new P(1, 2);
        new P("a", "b");
        new Q("z");
        new R(3);
        new R(3L);
    }
}
