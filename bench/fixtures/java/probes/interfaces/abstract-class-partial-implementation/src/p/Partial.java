package p;

public class Partial {
    interface Store { void put(String k); String get(String k); }
    static abstract class Base implements Store {
        public void put(String k) { get(k); }
    }
    static class Impl extends Base {
        public String get(String k) { return k; }
    }
    static class Other extends Base {
        public String get(String k) { return ""; }
        public void put(String k) {}
    }
    void use(Store s, Base b, Impl i) {
        s.put("a");
        s.get("b");
        b.put("c");
        i.put("d");
    }
}
