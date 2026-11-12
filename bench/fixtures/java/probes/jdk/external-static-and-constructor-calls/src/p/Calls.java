package p;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

public class Calls {
    static class Item implements Comparable<Item> {
        public int compareTo(Item o) { return 0; }
        public String toString() { return "i"; }
    }
    void use(List<Item> items) {
        Collections.sort(items);
        Collections.emptyList();
        Objects.requireNonNull(items);
        String.valueOf(1);
        Integer.parseInt("1");
        new ArrayList<Item>();
        new StringBuilder().append("x").append(1).toString();
        System.out.println(items);
        "lit".length();
        Math.max(1, 2);
    }
}
