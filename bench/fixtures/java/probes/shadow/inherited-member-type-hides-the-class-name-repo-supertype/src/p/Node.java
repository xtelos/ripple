package p;

class Base {
    static class Node { void run() {} }
}

/** Inside Node, the simple name Node is the member type it inherits from Base (JLS 6.4.1): n.run() calls Base.Node.run. */
public class Node extends Base {
    void run() {}

    void f(Node n) { n.run(); }
}
