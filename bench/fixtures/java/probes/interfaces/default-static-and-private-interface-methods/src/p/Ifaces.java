package p;

public class Ifaces {
    interface Greeter {
        String name();
        default String greet() { return prefix() + name(); }
        private String prefix() { return "hi "; }
        static Greeter of(String n) { return () -> n; }
        static void log(String s) {}
    }
    static class English implements Greeter {
        public String name() { return "en"; }
        void f() {
            greet();
            Greeter.log("x");
            Greeter.of("y").greet();
        }
    }
    void use(Greeter g, English e) {
        g.greet();
        e.greet();
        e.name();
        Greeter.of("z");
    }
}
