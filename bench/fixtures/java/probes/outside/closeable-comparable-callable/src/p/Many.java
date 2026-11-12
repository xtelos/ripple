package p;

import java.util.concurrent.Callable;
import java.util.function.Function;

class Work { static void go() {} }
class CloseBase { public void close() { Work.go(); } }
class Res extends CloseBase implements AutoCloseable {}
class Res2 extends Res {}
class OrderBase { public int compareTo(Object o) { Work.go(); return 0; } }
class Order extends OrderBase implements Comparable<Object> {}
class CallBase { public String call() { Work.go(); return ""; } }
class Job extends CallBase implements Callable<String> {}
class FnBase { public Integer apply(String s) { Work.go(); return 1; } }
class Fn extends FnBase implements Function<String, Integer> {}

public class Many {
    void h(AutoCloseable c, Comparable<Object> o, Callable<String> j, Function<String, Integer> f) throws Exception {
        c.close();
        o.compareTo("x");
        j.call();
        f.apply("s");
        new Res2().close();
    }
}
