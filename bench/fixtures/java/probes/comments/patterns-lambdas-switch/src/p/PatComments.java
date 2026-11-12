package p;

import java.util.function.Function;

class Item { void m() {} }
class Other { void m() {} }
record Pair(Item a, Object b) {}

public class PatComments {
    Other it = new Other();
    Other pair = new Other();
    void h(Object o) {
        if (o instanceof /* c */ Item /* d */ it) { it.m(); }
        if (o instanceof Pair(/* e */ Item a /* f */, /* g */ var b)) { a.m(); }
        Function<Item, Item> f = (/* h */ Item x /* i */) -> { x.m(); return x; };
        switch (o) {
            case /* j */ Pair(Item a, Object b) /* k */ -> a.m();
            case Item i /* l */ -> i.m();
            default -> {}
        }
        Function<Item, Integer> g = /* m */ x /* n */ -> { x.m(); return 1; };
    }
}
