package p;

import java.util.AbstractMap;
import java.util.Set;

class MyMap extends AbstractMap<String, String> {
    @Override
    public Set<Entry<String, String>> entrySet() {
        return null;
    }

    String k(Entry<String, String> e) {
        return e.getKey();
    }
}
