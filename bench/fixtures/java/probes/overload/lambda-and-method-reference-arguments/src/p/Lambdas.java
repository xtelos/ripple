package p;

import java.util.concurrent.Callable;
import java.util.function.Function;
import java.util.function.Supplier;

public class Lambdas {
    void run(Runnable r) {}
    <T> void run(Callable<T> c) {}
    void apply(Function<String, Integer> f) {}
    void apply(Supplier<String> s) {}
    void task() {}
    int number() { return 1; }
    String text() { return ""; }
    void use() throws Exception {
        run(() -> {});
        run(() -> task());
        run(this::task);
        apply(String::length);
        apply(this::text);
        apply(s -> s.length());
        apply(() -> text());
        Runnable r = () -> task();
        r.run();
    }
}
