package p;

import java.util.ArrayList;
import java.util.List;

public class Vars {
    static class Item { void add(int x) {} }
    static class Money { void add(int x) {} }
    static Item make() { return new Item(); }
    void use(List<Money> moneys) {
        var a = new Item();
        a.add(1);
        var b = make();
        b.add(2);
        var c = moneys.get(0);
        c.add(3);
        List<Item> items = new ArrayList<>();
        items.add(new Item());
        for (var m : moneys) {
            m.add(4);
        }
        var d = (Money) null;
        d.add(5);
    }
}
