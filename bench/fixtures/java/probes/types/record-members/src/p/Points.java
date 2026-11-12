package p;

public class Points {
    record Point(int x, int y) {
        Point {
            check(x);
        }
        static void check(int v) {}
        int sum() { return x() + y(); }
        public int y() { return y; }
        static Point origin() { return new Point(0, 0); }
    }
    void use(Point pt) {
        pt.x();
        pt.y();
        pt.sum();
        pt.toString();
        pt.hashCode();
        Point.origin();
        new Point(1, 2);
    }
}
