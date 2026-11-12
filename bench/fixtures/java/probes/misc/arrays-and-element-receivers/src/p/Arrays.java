package p;

public class Arrays {
    static class Item { void add(int x) {} }
    static void take(Item[] items) {}
    static void take(Item item) {}
    static void many(Item... items) {}
    void use(Item[] items, int[][] grid, Item[][] table) {
        items[0].add(1);
        table[0][1].add(2);
        items.clone();
        grid[0].clone();
        take(items);
        take(items[0]);
        take(new Item[2]);
        many(items);
        many(items[0], items[1]);
        many();
        for (Item i : items) {
            i.add(3);
        }
        int n = items.length;
        new Item[] { new Item() }[0].add(4);
    }
}
