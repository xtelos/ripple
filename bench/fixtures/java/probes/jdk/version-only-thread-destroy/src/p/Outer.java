package p;

/** Thread.destroy() is declared by JDK 8 and by no later JDK: destroy() in Q is Thread's on 8 and Outer's on 11 and later. */
public class Outer {
    void destroy() {}

    void helper() {}

    public class Q extends Thread {
        void f() {
            destroy();
            helper();
        }
    }
}
