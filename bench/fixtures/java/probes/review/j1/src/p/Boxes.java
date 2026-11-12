package p;

class Boxes {
    void f(int x) {}
    void f(Integer x) {}
    void f(long x) {}
    void f(Object x) {}

    void v(String... s) {}
    void v(String a) {}

    void g() {
        f(1);
        f('c');
        f((short) 1);
        f(1L);
        Integer i = 1;
        f(i);
        f(null);
        f((Object) 1);
        v("a");
        v();
        v("a", "b");
    }
}
