package p;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

@Retention(RetentionPolicy.RUNTIME)
@interface Uses { Class<?> value(); }

sealed interface Shape15 permits Circle15, Square15 {}
record Circle15(double r) implements Shape15 { double area() { return r * r; } }
record Square15(double s) implements Shape15 { double area() { return s * s; } }
class Res15 implements AutoCloseable { public void close() {} void use() {} }
class Box15 { class In { void q() {} } static class SIn { void q() {} } }

@Uses(Box15.class)
public class K15 {
    double f(Shape15 sh, Object o, int k) {
        double d = switch (sh) {
            case Circle15 c when c.area() > 1 -> c.area();
            case Circle15 c -> { double a = c.area(); yield a; }
            case Square15 s -> s.area();
        };
        int z = switch (k) {
            case 1 -> { Res15 r = new Res15(); r.use(); yield 1; }
            default -> 0;
        };
        try (Res15 r = new Res15()) {
            r.use();
            r.close();
        }
        Box15 b = new Box15();
        b.new In().q();
        new Box15.SIn().q();
        p.util.Tool.go();
        if (o instanceof Circle15(double rr) && rr > 0) {}
        return d;
    }
}
