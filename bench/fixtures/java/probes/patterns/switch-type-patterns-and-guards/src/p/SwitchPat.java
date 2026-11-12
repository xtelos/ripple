package p;

class A { void use() {} }
class B { void use() {} }
class Fa { void use() {} }

public class SwitchPat {
    Fa a = new Fa();
    String h(Object o) {
        switch (o) {
            case A a when a != null -> a.use();
            case B b -> b.use();
            default -> a.use();
        }
        return switch (o) {
            case A x -> { x.use(); yield "a"; }
            case B x -> { x.use(); yield "b"; }
            default -> "d";
        };
    }
}
