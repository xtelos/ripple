package p;

public class Pair {
    record Item(String name, int qty) {
        Item(String name) {
            this(name, 1);
        }
        Item() {
            this("none");
        }
    }
    void use() {
        new Item();
        new Item("a");
        new Item("a", 2);
    }
}
