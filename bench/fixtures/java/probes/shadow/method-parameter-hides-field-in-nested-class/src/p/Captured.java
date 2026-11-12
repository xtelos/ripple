package p;

public class Captured {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    Money v = new Money();
    Runnable make(Item v) {
        class Local implements Runnable {
            public void run() {
                v.add(1);
            }
        }
        return new Runnable() {
            public void run() {
                v.add(2);
            }
        };
    }
    void other() {
        v.add(3);
    }
}
