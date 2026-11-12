package p.b;

import p.a.Shape;

public class Use implements Shape {
    public void draw(String s) {}
    void f(Shape shape) {
        shape.draw("x");
        draw("y");
    }
}
