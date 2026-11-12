package p;

import java.lang.annotation.ElementType;
import java.lang.annotation.Target;

@Target(ElementType.TYPE_USE)
@interface Nullable {}

class Util {
    static String name(Class<?> c, String m, Class<?> @Nullable... params) { return m; }
    static String name(String a, String b, String c) { return a; }
    Util(Class<?> c, Class<?> @Nullable... params) {}
    Util(String s) {}
}

public class Annotated {
    String h(Class<?> c) {
        new Util(c, String.class);
        new Util("s");
        return Util.name(c, "x") + Util.name(c, "x", String.class) + Util.name("a", "b", "c");
    }
}
