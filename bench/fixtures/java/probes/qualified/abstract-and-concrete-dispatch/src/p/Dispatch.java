package p;

public class Dispatch {
    static abstract class Shape {
        abstract double area();
        double twice() { return area() * 2; }
    }
    static class Sq extends Shape {
        double area() { return 1; }
    }
    static class Circ extends Shape {
        double area() { return 2; }
        double twice() { return 4; }
    }
    static final class Fin extends Shape {
        double area() { return 3; }
    }
    void use(Shape s, Sq sq, Fin fin) {
        s.area();
        s.twice();
        sq.area();
        sq.twice();
        fin.area();
        new Circ().twice();
    }
}
