package p;

public class Comb {
    static class Base {
        void helper(int x) {}
    }
    void helper(String s) {}
    void other() {}
    class Inner extends Base {
        void f() {
            helper(1);
            other();
        }
    }
    class Plain {
        void f() {
            helper("x");
            other();
        }
    }
    class Deeper extends Plain {
        class Deepest {
            void g() {
                other();
                helper("y");
            }
        }
    }
}
