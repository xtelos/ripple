package p;

public class AnonExt {
    static class Helper {
        static int make() { return 1; }
    }
    void f() {
        Runnable r = new Runnable() {
            int n = Helper.make();
            public void run() {
                Helper.make();
            }
        };
        r.run();
        Thread t = new Thread() {
            { Helper.make(); }
            public void run() {
                Helper.make();
            }
        };
        t.start();
    }
}
