package p;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

class Gen {
    void f(List<String> a) {}

    void f(Collection<Integer> b) {}

    void g() {
        List<Integer> x = new ArrayList<>();
        f(x);
    }
}
