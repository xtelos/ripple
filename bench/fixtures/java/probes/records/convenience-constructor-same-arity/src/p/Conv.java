package p;

import java.util.UUID;

record Id(String value) {
    Id(UUID u) { this(u.toString()); }
}
record R(int v) {
    R(long w) { this((int) w); }
}
record P(int x, int y) {
    P(String a, String b) { this(1, 2); }
}
record Wide(int a, int b, int c) {
    Wide(int a, int b) { this(a, b, 0); }
    Wide(String s) { this(1, 2, 3); }
}

public class Conv {
    void h(UUID u) {
        new Id("x");
        new Id(u);
        new R(3);
        new R(3L);
        new P(1, 2);
        new P("a", "b");
        new Wide(1, 2, 3);
        new Wide(1, 2);
        new Wide("s");
    }
}
