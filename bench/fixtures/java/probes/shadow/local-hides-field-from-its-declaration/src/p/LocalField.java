package p;

public class LocalField {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money total = new Money();
    void f() {
        total.add(1);
        Item total = new Item();
        total.add(2);
        this.total.add(3);
    }
    void g(Item total) {
        total.add(4);
        this.total.add(5);
    }
}
