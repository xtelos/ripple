package p;

public class Chain {
    static class Root {
        void walk() {}
        void run(String s) {}
    }
    static class Mid extends Root {
        void run(int n) {}
        class Leaf {
            void f() {
                walk();
                run(1);
                run("a");
            }
        }
    }
}
