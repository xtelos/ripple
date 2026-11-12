package p;

public class Tree {
    static class Base {
        void grow() {}
    }
    static class Node extends Base {
        static class Base {
            void grow() {}
        }
        void f() {
            grow();
        }
    }
    static class Leaf extends Node.Base {
        void g() {
            grow();
        }
    }
}
