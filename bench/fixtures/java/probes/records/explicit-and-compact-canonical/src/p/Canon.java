package p;

record Explicit(int a, int b) {
    Explicit(int a, int b) { this.a = a; this.b = b; }
    Explicit(int a) { this(a, 0); }
}
record Compact(int a, String s) {
    Compact {
        if (a < 0) throw new IllegalArgumentException(s);
    }
    Compact(String s) { this(0, s); }
}
record Plain(int a) {}
record Empty() {
    Empty(int ignored) { this(); }
}

public class Canon {
    void h() {
        new Explicit(1, 2);
        new Explicit(1);
        new Compact(1, "s");
        new Compact("s");
        new Plain(1);
        new Empty();
        new Empty(3);
    }
}
