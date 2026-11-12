package p;

import java.util.Map;
import java.util.Set;

public abstract class Store implements Map<String, String> {
    static class Entry {
        String getValue() { return ""; }
    }
    void f(Entry e) {
        e.getValue();
    }
    void g(Set<Entry> entries) {
        for (Entry e : entries) {
            e.getValue();
        }
    }
}
