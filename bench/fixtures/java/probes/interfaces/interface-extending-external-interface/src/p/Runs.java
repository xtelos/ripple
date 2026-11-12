package p;

public class Runs {
    interface Task extends Runnable {
        void extra();
    }
    static class Impl implements Task {
        public void run() {}
        public void extra() {}
    }
    void use(Task t, Impl i, Runnable r) {
        t.run();
        t.extra();
        i.run();
        r.run();
    }
}
