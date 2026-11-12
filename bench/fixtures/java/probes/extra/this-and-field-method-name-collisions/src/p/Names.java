package p;

public class Names {
    static class Item { void add(int x) {} }
    Item add = new Item();
    void add(int x) {}
    void use() {
        add(1);
        add.add(2);
        this.add.add(3);
        this.add(4);
        Runnable r = this::use;
        r.run();
    }
}
