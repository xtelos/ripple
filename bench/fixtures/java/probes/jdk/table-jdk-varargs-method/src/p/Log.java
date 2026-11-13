package p;

import java.io.PrintStream;
import java.util.concurrent.CompletableFuture;

/** A JDK varargs method takes as many arguments as a repo method of its name does (JLS 15.12.2.4: phase 3 comes last). */
public class Log extends PrintStream {
    static class Item {}

    Log() {
        super(System.out);
    }

    void printf(Item i) {}

    void f(Item i) {
        printf("x");
        printf(i);
        printf("%s %s", i, i);
    }
}

class Fut extends CompletableFuture<String> {
    static void allOf() {}

    void g(CompletableFuture<?> a) {
        allOf();
        allOf(a);
        allOf(a, a);
    }
}
