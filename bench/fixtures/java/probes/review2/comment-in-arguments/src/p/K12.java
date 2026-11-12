package p;

class Box12 {
    Box12(int a) {}
    Box12(int a, int b) {}
}

public class K12 {
    void f(int a) {}
    void f(int a, int b) {}
    void g() {
        f(1 /* one */);
        f(1, // two
          2);
        f(/* nothing */ 3);
        new Box12(4 /* four */);
        this.f(5 /* five */);
    }
}
