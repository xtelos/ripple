package p;

import java.util.List;
import java.util.function.Supplier;

class Plain { public void run() {} }
class Task extends Plain {}
class Fine implements Comparable<Fine> { public int compareTo(Fine o) { return 0; } }

public class NoImpl {
    void h(List<String> l, Supplier<String> s, Runnable r) {
        l.add("x");
        l.size();
        s.get();
        r.run();
        "abc".length();
        Math.abs(1);
    }
}
