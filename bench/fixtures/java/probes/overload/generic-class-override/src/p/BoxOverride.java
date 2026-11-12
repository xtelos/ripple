package p;

public class BoxOverride {
    static class Box<T> {
        void put(T t) {}
        T get() { return null; }
    }
    static class StrBox extends Box<String> {
        void put(String s) {}
    }
    void use(Box<String> box, StrBox strBox, Box<Integer> ints) {
        box.put("x");
        strBox.put("x");
        ints.put(1);
        box.get().length();
        strBox.get().length();
    }
}
