package p;

import java.util.List;
import java.util.Set;

public class Wild {
    void f(List<?> l) {}
    void f(Set<String> c) {}
    void g(List<? extends Number> l) {}
    void g(Object o) {}
    void use(List<String> strings, List<Integer> ints, List raw, Object o) {
        f(strings);
        f(ints);
        f(raw);
        g(ints);
        g(strings);
        g(o);
    }
}
