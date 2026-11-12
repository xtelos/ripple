package p;

import java.util.List;
import java.util.function.Function;
import java.util.function.Supplier;

public class Refs {
    static class Item {
        Item() {}
        Item(String s) {}
        String name() { return ""; }
    }
    static Item make(String s) { return new Item(s); }
    void use(List<String> names) {
        Function<String, Item> f = Item::new;
        Supplier<Item> s = Item::new;
        Function<String, Item> g = Refs::make;
        names.stream().map(Item::new).map(Item::name).forEach(this::take);
        f.apply("x");
        s.get();
        g.apply("y");
    }
    void take(String s) {}
}
