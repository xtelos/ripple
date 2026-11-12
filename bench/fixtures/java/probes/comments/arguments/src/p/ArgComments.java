package p;

class Box {
    Box(int a) {}
    Box(int a, int b) {}
}

public class ArgComments {
    void f(int a) {}
    void f(int a, int b) {}
    void g(String s) {}
    void g(String s, String t) {}
    void h() {
        f(1 /* one */);
        f(/* nothing */ 3);
        f(1, // two
            2);
        this.f(5 /* five */);
        new Box(4 /* four */);
        new Box(4, /* x */ 5 /* y */);
        g(/**/ "a");
        g("a" /* a */, /* b */ "b");
        f(
            // leading
            7
            // trailing
        );
    }
}
