package p;

public class GenericMethods {
    static <T> T id(T t) { return t; }
    static <T extends Comparable<T>> T max(T a, T b) { return a; }
    static void id(String s) {}
    <T> void put(T t) {}
    void put(Object o, int n) {}
    void use() {
        id("x");
        GenericMethods.<String>id("y");
        id(1);
        max("a", "b");
        max(1, 2);
        put("x");
        put("x", 1);
    }
}
