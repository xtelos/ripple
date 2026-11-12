package p;

import java.util.Iterator;

public class Iter implements Iterable<String> {
    public Iterator<String> iterator() { return null; }
    void use(Iterable<String> it, Iter mine) {
        it.iterator();
        mine.iterator();
        it.forEach(s -> {});
        mine.forEach(s -> {});
    }
}
