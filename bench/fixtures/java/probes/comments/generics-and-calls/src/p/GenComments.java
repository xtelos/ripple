package p;

import java.util.ArrayList;
import java.util.List;

class Item { void m() {} }
class Repo<T> {
    void save(T t /* a */) {}
    void save(T t, boolean flush /* b */) {}
    <R> R map(R r /* c */) { return r; }
}

public class GenComments {
    Repo<Item> repo = new Repo</* x */ Item>();
    List<Item> items = new ArrayList<>();
    void h(Item it) {
        repo.save(it /* y */);
        repo.save(it, /* z */ true);
        repo.<Item>map(/* w */ it).m();
        items.add(it /* v */);
        items.get(0 /* u */).m();
    }
}
