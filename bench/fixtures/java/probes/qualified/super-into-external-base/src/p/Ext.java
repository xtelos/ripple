package p;

import java.util.ArrayList;

public class Ext extends ArrayList<String> {
    public boolean add(String s) {
        return super.add(s);
    }
    public String toString() {
        return super.toString() + size();
    }
    void f() {
        add("x");
        toString();
        this.add("y");
        super.size();
        hashCode();
    }
}
