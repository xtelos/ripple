package p;

import java.util.List;
import java.util.Map;

class Item { void m() {} }
class Other { void m() {} }
class Sink {
    void put(List</* e */ Item> xs) {}
    void put(Map.Entry<String, /* v */ Integer> e) {}
    void put(Item /* c */ ... items) {}
    void put(String s) {}
}

public class TypeComments {
    java.lang./* c */ String text = "x";
    Item /* c */ first = new /* new */ Item();
    void h(List<Item> xs, Item /* c */ it, Other /* o */ other) {
        Sink s = new /* sink */ Sink();
        s.put(xs);
        s.put(text);
        s.put(it, it);
        first /* a */ . /* b */ m();
        it.m();
        other.m();
        ((/* c */ Item) it).m();
        (/* d */ it).m();
    }
}
