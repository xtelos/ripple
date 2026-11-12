package p;

class Item { void add(int n) {} }
class Money { void add(int n) {} }
record Pair(Item first, Object second) {}
sealed interface Shape permits Circle, Square {}
record Circle(Item c) implements Shape {}
record Square(Item s) implements Shape {}

public class SwitchRec {
    Money first = new Money();
    Money c = new Money();
    Money s = new Money();
    void h(Object o, Shape sh) {
        switch (o) {
            case Pair(Item first, Object b) -> first.add(3);
            default -> first.add(4);
        }
        switch (sh) {
            case Circle(Item c) -> c.add(5);
            case Square(Item s) -> s.add(6);
        }
        String r = switch (o) {
            case Pair(Item first, var b) when first != null -> { first.add(7); yield "p"; }
            default -> "d";
        };
    }
}
