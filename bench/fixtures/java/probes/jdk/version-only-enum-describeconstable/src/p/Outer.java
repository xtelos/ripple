package p;

/** Enum.describeConstable() is declared from JDK 12: in Op it is Enum's on 17 and later, and Outer's static one on 8. */
public class Outer {
    static Object describeConstable() {
        return "outer";
    }

    public enum Op {
        ADD;

        Object f() {
            return describeConstable();
        }
    }
}
