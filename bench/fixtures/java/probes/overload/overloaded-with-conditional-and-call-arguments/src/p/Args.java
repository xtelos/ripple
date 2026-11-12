package p;

public class Args {
    void f(String s) {}
    void f(Object o) {}
    String name() { return ""; }
    Object any() { return null; }
    int count() { return 1; }
    void count(int n) {}
    void count(String s) {}
    void use(boolean flag, String s) {
        f(name());
        f(any());
        f(flag ? "a" : "b");
        f(s + 1);
        f(name() + name());
        count(count());
        count(name());
        f(this);
    }
}
