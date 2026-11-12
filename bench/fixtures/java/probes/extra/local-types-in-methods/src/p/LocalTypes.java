package p;

public class LocalTypes {
    interface Sink { void put(String s); }
    void f() {
        record Pair(String a, int b) {
            String show() { return a + b; }
        }
        enum Mode { ON, OFF; boolean on() { return this == ON; } }
        interface Local { void go(); }
        Pair pair = new Pair("a", 1);
        pair.show();
        pair.a();
        Mode.ON.on();
        Mode.valueOf("OFF");
        Local l = () -> pair.show();
        l.go();
        class Impl implements Sink {
            public void put(String s) { pair.show(); }
        }
        new Impl().put("x");
    }
}
