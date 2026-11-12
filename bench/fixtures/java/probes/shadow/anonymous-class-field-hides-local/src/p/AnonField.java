package p;

public class AnonField {
    static class Money { void add(int x) {} }
    static class Item { void add(int x) {} }
    static abstract class Task { abstract void run(); }
    void f() {
        Money v = new Money();
        Task t = new Task() {
            Item v = new Item();
            void run() {
                v.add(1);
            }
        };
        v.add(2);
    }
}
