package p;

import java.awt.Point;

/**
 * Point is java.desktop's, so it is not in the JDK table: a type that is not there is unknown, never "declares nothing".
 * getX() in Q is Point.getX(), not the enclosing Outer's (JLS 15.12.1: the innermost class that has a member of that name).
 */
public class Outer {
    double getX() {
        return 0;
    }

    public class Q extends Point {
        double f() {
            return getX();
        }
    }
}
