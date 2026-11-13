package p;

import java.util.ArrayList;
import java.util.LinkedList;
import java.util.Queue;
import java.util.Set;

/** Fifo is a Queue (through LinkedList) and Tags is not: only a call through Queue may run a repo method. */
class Fifo extends LinkedList<String> {
    public boolean add(String s) {
        return true;
    }
}

class Tags extends ArrayList<String> {
    public boolean add(String s) {
        return true;
    }
}

public class Use {
    boolean q(Queue<String> q) {
        return q.add("x");
    }

    boolean s(Set<String> s) {
        return s.add("x");
    }

    boolean t(Tags t) {
        return t.add("x");
    }
}
