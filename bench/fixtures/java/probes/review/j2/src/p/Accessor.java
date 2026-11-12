package p;

record Point(int x, int y) {
    Point {
        if (x < 0) throw new IllegalArgumentException();
    }

    int x(int scale) {
        return x * scale;
    }
}

enum Color {
    RED {
        @Override
        String label() {
            return "r";
        }
    },
    BLUE;

    String label() {
        return name();
    }
}

sealed interface Shape permits Sq, Ci {}

record Sq(int side) implements Shape {}

record Ci(int r) implements Shape {}

class Accessor {
    int f(Point p, Color c, Shape s) {
        c.label();
        Color.valueOf("RED");
        int a = switch (s) {
            case Sq q -> q.side();
            case Ci ci -> ci.r();
        };
        return p.x() + p.x(2) + new Point(1, 2).y() + a;
    }
}
