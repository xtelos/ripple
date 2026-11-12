package p;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

public class MyList extends ArrayList<String> {
    public boolean add(String s) {
        return super.add(s);
    }
    static void use(List<String> l, Collection<String> c, ArrayList<String> a, MyList m) {
        l.add("x");
        c.add("y");
        a.add("z");
        m.add("w");
        l.size();
    }
}
