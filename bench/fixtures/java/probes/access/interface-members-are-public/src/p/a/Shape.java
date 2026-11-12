package p.a;

public interface Shape {
    void draw(String s);
    default void draw(Object o) {}
}
