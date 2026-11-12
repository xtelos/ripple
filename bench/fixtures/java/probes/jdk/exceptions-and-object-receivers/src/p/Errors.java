package p;

public class Errors {
    static class MyEx extends Exception {
        public String getMessage() { return "mine"; }
    }
    static class Thing {
        public String toString() { return "thing"; }
        public boolean equals(Object o) { return true; }
        public int hashCode() { return 1; }
    }
    void f(Throwable t, Object o) {
        try {
            throw new MyEx();
        } catch (Exception e) {
            e.getMessage();
        }
        t.getMessage();
        o.toString();
        o.equals(o);
        o.hashCode();
        o.getClass();
    }
}
