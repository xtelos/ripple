package p;

import java.util.List;
import java.util.Map;

public class Params {
    static class Entry<K, V> {
        <X> void put(K k, V v, X x) {}
        void put(Map.Entry<K, V> e) {}
        void put(List<K>[] keys) {}
    }
    void use(Entry<String, String> e, Map.Entry<String, String> m) {
        e.put(m);
        e.put("a", "b", 1);
    }
}
