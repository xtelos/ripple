package p;

class Node {
    Node next() { return this; }
    Node self() { return this; }
    void done() {}
}

public class Chains {
    Node head = new Node();
    void h(Node n) {
        n /* a */ . /* b */ next() // c
            . /* d */ self() /* e */
            .done();
        head.
            // f
            next()
            /* g */ .done();
        this /* h */ . /* i */ head /* j */ . /* k */ self();
        new Node() /* l */ .next().self();
        ((Node) /* m */ n).done();
    }
}
