package p;

public class Shadows {
    void run() {}
    void helper(String s) {}
    void use() {
        Runnable r = new Runnable() {
            public void run() {
                Shadows.this.run();
            }
            void helper(int n) {}
            void inner() {
                helper(1);
            }
        };
        Runnable q = () -> run();
        r.run();
        q.run();
    }
}
