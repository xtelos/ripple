package p;

interface Shape { double area(); default String label() { return "shape"; } }
record Circle(double r) implements Shape {
    public double area() { return r * r; }
    static Circle unit() { return new Circle(1); }
}
record Square(double s) implements Shape {
    public double area() { return s * s; }
    public String label() { return "square"; }
}

public class RecMethods {
    void h(Shape s, Circle c, Square q) {
        s.area();
        s.label();
        c.label();
        q.label();
        Circle.unit().area();
        new Square(2).area();
    }
}
