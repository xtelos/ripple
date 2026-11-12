package p;

class Item { void add(int n) {} }
class Money { void add(int n) {} }
record Box(Item item) {}
record Pair(Box left, Object right) {}

public class Nested {
    Money item = new Money();
    Money left = new Money();
    Money inner = new Money();
    void h(Object o) {
        if (o instanceof Pair(Box(Item item), Item right)) {
            item.add(1);
            right.add(2);
        }
        if (o instanceof Pair(Box(var inner), var left)) {
            left.toString();
        }
        if (o instanceof Pair(Box left, Object right) && left.item() != null) {
            left.item().add(3);
        }
    }
}
