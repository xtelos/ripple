package p;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;

public class Erasure {
    void f(List<String> l) {}
    void f(Collection<Integer> c) {}
    void g(Map<String, String> m) {}
    void g(Collection<Integer> c) {}
    void h(List<String> l, int n) {}
    void h(List<Integer> l, long n) {}
    void use(List<Integer> ints, List<String> strings, ArrayList<Integer> list) {
        f(ints);
        f(strings);
        g(ints);
        h(strings, 1);
        h(ints, 1L);
    }
}
