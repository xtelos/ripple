package p;

public class Nodes {
    static class List<T> {
        Node<T> head;
        static class Node<T> {
            void link(Node<T> next) {}
        }
        void add(T t) {}
        void use(Node<T> n, java.util.List<String> real) {
            n.link(n);
            real.add("x");
            add(null);
        }
    }
    void f(List<String> l) {
        l.add("x");
    }
}
