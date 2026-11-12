package p;

public class Varargs {
    void v(String s) {}
    void v(String... s) {}
    void v(Object... o) {}
    void w(int a, String... rest) {}
    void w(int a, int b) {}
    void use(String[] arr, Object[] objs) {
        v();
        v("a");
        v("a", "b");
        v(arr);
        v(objs);
        v(1, 2);
        w(1);
        w(1, 2);
        w(1, "x");
        w(1, "x", "y");
    }
}
