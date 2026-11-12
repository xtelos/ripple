package p;

public class Casts {
    static class Item { void add(int x) {} }
    static class Money extends Item { void add(int x) {} void pay() {} }
    void use(Object o, Item i, boolean b) {
        ((Item) o).add(1);
        ((Money) i).pay();
        ((Money) i).add(2);
        (b ? i : new Money()).add(3);
        ((Item) (Object) i).add(4);
        Item x = b ? i : null;
        x.add(5);
    }
}
