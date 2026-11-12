package p;

public class Statics {
    static void helper() {}
    static void helper(int n) {}
    static class Nested {
        static void helper(String s) {}
        void f() {
            helper("x");
            Statics.helper();
            Statics.helper(1);
        }
    }
    static class Other {
        void f() {
            helper();
            helper(2);
        }
    }
}
