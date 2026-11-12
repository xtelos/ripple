package p;

import q.Item;

public class Single {
    void f(Item item) {
        item.add(1);
        new Item().add(2);
    }
}
