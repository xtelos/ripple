package p;

import java.util.List;

public class Ctors {
    Ctors() {}
    Ctors(String s) {}
    Ctors(Object o) {}
    Ctors(int i, String s) {}
    Ctors(String s, int i) {}
    Ctors(List<String> l) {}
    Ctors(String... rest) {}
    static void use(List<String> list) {
        new Ctors();
        new Ctors("x");
        new Ctors(1);
        new Ctors(1, "x");
        new Ctors("x", 1);
        new Ctors(list);
        new Ctors("a", "b");
        new Ctors((Object) "x");
    }
}
