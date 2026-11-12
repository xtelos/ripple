package p;

import java.util.List;

public class Renamed {
    static abstract class Handler {
        abstract <T> void handle(T value);
        abstract <T extends Number> void number(T value);
        abstract void list(List<String> l);
    }
    static class Impl extends Handler {
        <U> void handle(U value) {}
        <V extends Number> void number(V value) {}
        void list(List<String> l) {}
    }
    void use(Handler h, Impl i, List<String> l) {
        h.handle("x");
        i.handle(1);
        h.number(1);
        i.number(1.5);
        i.list(l);
        h.list(l);
    }
}
