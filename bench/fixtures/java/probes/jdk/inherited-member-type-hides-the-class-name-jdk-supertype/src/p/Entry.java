package p;

import java.util.AbstractMap;
import java.util.Map;
import java.util.Set;

/** Inside this class the simple name Entry is the member type it inherits from Map (JLS 6.4.1), not this class: first(e) calls Map.Entry.getKey. */
public class Entry extends AbstractMap<String, String> {
    public Set<Map.Entry<String, String>> entrySet() { return null; }
    public String getKey() { return "mine"; }

    Object first(Entry e) { return e.getKey(); }
}
