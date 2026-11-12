package p;

import java.util.AbstractMap;
import java.util.Map;
import java.util.Set;

public class MyMap extends AbstractMap<String, String> {
    public Set<Map.Entry<String, String>> entrySet() { return null; }
    void f(Entry<String, String> e, Map.Entry<String, String> real) {
        e.getKey();
        real.getKey();
    }
    static class Nested {
        void g(Entry<String, String> e) {
            e.getKey();
        }
    }
}
