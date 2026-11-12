package p;

public class Anon {
    static abstract class Task {
        Task(String name) {}
        abstract void run();
        void helper() {}
    }
    void helper() {}
    void outerOnly() {}
    void f() {
        Task t = new Task("x") {
            void run() {
                helper();
                outerOnly();
            }
        };
        t.run();
        t.helper();
    }
}
