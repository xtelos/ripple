package p;

public class Fn {
    @FunctionalInterface
    interface Step { Step next(); default Step twice() { return next().next(); } }
    static Step build() { return () -> null; }
    void use(Step s) {
        s.next().next();
        s.twice();
        build().twice();
        build().next().twice();
    }
}
