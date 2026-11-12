package p;

public class Varargs {
    void v(String /* c */ ... xs) {}
    void v(int a, String /* d */ ... xs) {}
    void w(int a) {}
    void w(int a, int b) {}
    void w(int... rest) {}
    void h() {
        v("a" /* a */);
        v(1 /* b */, "c");
        v(/* none */);
        w(1 /* one */);
        w(1, 2 /* two */);
        w(1, 2, 3 /* three */);
    }
}
