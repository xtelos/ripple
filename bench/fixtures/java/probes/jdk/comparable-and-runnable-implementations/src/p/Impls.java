package p;

public class Impls {
    static class Item implements Comparable<Item>, Runnable {
        public int compareTo(Item o) { return 0; }
        public void run() {}
    }
    void use(Comparable<Item> c, Runnable r, Item i) {
        c.compareTo(i);
        r.run();
        i.compareTo(i);
        i.run();
        new Thread(r).start();
    }
}
