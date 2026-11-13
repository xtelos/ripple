package p;

import java.util.HashMap;
import java.util.Map;

/**
 * HashMap inherits the member type Map.Entry, which hides the p.Entry of the same package inside Counts (JLS 6.4.1, 8.5).
 * HashMap.Node is package-private: it is no member of Counts, so Node is p.Node.
 */
public class Counts extends HashMap<String, Integer> {
    Object f(Entry e) {
        return e.getKey();
    }

    String g(Node n) {
        return n.getKey();
    }

    String h(java.util.HashMap.Entry<String, Integer> e) {
        return e.getKey();
    }
}

class Pair implements Map.Entry<String, String> {
    public String getKey() {
        return "k";
    }

    public String getValue() {
        return "v";
    }

    public String setValue(String s) {
        return s;
    }
}

class Use {
    String a(Map.Entry<String, String> e) {
        return e.getKey();
    }
}
