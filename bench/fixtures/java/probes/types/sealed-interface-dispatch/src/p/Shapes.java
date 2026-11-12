package p;

public class Shapes {
    sealed interface Shape permits Circle, Square {
        double area();
        default String label() { return "shape"; }
    }
    record Circle(double r) implements Shape {
        public double area() { return r; }
    }
    record Square(double s) implements Shape {
        public double area() { return s; }
        public String label() { return "square"; }
    }
    void use(Shape shape, Circle c, Square s) {
        shape.area();
        shape.label();
        c.area();
        c.label();
        s.label();
    }
}
