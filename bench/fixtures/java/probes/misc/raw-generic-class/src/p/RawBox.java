package p;

public class RawBox {
    static class Box<T> {
        void put(T t) {}
        void put(String s, int n) {}
        T get() { return null; }
    }
    @SuppressWarnings({"rawtypes", "unchecked"})
    void use() {
        Box raw = new Box();
        raw.put("x");
        raw.put("x", 1);
        raw.get();
        Box<String> typed = new Box<>();
        typed.put("y");
        typed.get();
    }
}
