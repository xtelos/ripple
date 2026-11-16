package p;

import java.io.*;
import java.util.*;

class Item {}

/** Writer and ArrayList come from on-demand imports: javac says `record` is Ledger's, ripple (strict) does not use the table for them. */
class Ledger extends Writer {
    public void write(char[] b, int o, int l) {}

    public void flush() {}

    public void close() {}

    void record(Item i) {}
}

class Names extends ArrayList<String> {
    void record(Item i) {}

    public boolean add(String s) {
        return true;
    }
}

public class Use {
    void ledger(Ledger l, Item i) throws IOException {
        l.record(i);
        l.write(7);
        l.flush();
    }

    void names(Names n, Item i) {
        n.record(i);
        n.add("x");
        n.size();
    }

    boolean set(Set<String> s) {
        return s.add("x");
    }

    boolean queue(Queue<String> q) {
        return q.add("x");
    }

    int collections(List<String> list) {
        Collections.sort(list);
        return list.size() + Collections.max(list).length() + Objects.hash(list);
    }

    String entries(Map<String, String> m) {
        StringBuilder out = new StringBuilder();
        for (Map.Entry<String, String> e : m.entrySet()) {
            out.append(e.getKey()).append(e.getValue());
        }
        return out.toString();
    }

    Names fresh() {
        return new Names();
    }
}
