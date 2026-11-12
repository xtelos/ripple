package p;

import java.util.List;

public class Repo {
    static class Store<T> {
        void put(T t) {}
        void put(T t, int n) {}
        void put(List<T> all) {}
        void put(char[] key) {}
    }
    static class Names extends Store<String> {
        void put(String s, int n) {}
    }
    void use(Store<Integer> ints, Names names, Store raw) {
        ints.put(1);
        ints.put(1, 2);
        ints.put(new char[0]);
        names.put("n", 1);
        names.put("n");
        raw.put(new char[0]);
        raw.put(null, 1);
    }
}
