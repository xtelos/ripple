package p;

import java.io.*;
import java.util.*;
import q.Deque;

class Item {}

/** A type of the compilation unit beats both imports (JLS 6.4.1). */
class Writer {
    void write(Item i) {}
}

class A {
    void f(Writer w, Item i) {
        w.write(i);
    }
}

/** A member type, a type variable and a local class each beat the on-demand import of java.util. */
class B {
    static class Queue {
        void add(Item i) {}
    }

    void f(Queue q, Item i) {
        q.add(i);
    }
}

class C<Set> {
    void f(Set s) {
        s.toString();
    }
}

class D {
    void f(Item i) {
        class List {
            void add(Item x) {}
        }
        List l = new List();
        l.add(i);
    }
}

/** A member type a repo supertype declares is inherited and beats the import. */
class Base {
    static class Map {
        void put(Item i) {}
    }
}

class E extends Base {
    void f(Map m, Item i) {
        m.put(i);
    }
}

/** A single-type import beats the on-demand one of a package that has the name too, and so does a type of the same package in another file. */
class F {
    void f(Deque d, Item i, Optional o) {
        d.push(i);
        o.get(i);
    }
}
