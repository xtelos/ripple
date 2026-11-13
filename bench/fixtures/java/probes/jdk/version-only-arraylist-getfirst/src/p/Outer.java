package p;

import java.util.ArrayList;

/** ArrayList.getFirst() is declared from JDK 21: getFirst() in Q is ArrayList's on 21 and later, Outer's before. */
public class Outer {
    Object getFirst() {
        return "outer";
    }

    public class Q extends ArrayList<String> {
        Object f() {
            return getFirst();
        }
    }
}
