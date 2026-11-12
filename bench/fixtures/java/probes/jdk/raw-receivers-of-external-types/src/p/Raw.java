package p;

import java.util.ArrayList;
import java.util.List;

public class Raw {
    static class Mine extends ArrayList<String> {
        public boolean add(String s) { return true; }
    }
    @SuppressWarnings({"rawtypes", "unchecked"})
    void use() {
        List l = new Mine();
        l.add("x");
        l.size();
        Object o = new Mine();
        o.toString();
        ArrayList raw = new Mine();
        raw.add(1);
    }
}
