package p;

import java.util.function.Supplier;
import java.util.Iterator;

class RunBase { public void run() { Helper.work(); } }
class Task extends RunBase implements Runnable {}
class Helper { static void work() {} }
class GetBase { public String get() { Helper.work(); return "n"; } }
class Sup extends GetBase implements Supplier<String> {}
abstract class CloseBase { public void close() { Helper.work(); } }
class Res extends CloseBase implements AutoCloseable {}
class NextBase { public boolean hasNext() { Helper.work(); return false; } }
abstract class It extends NextBase implements Iterator<String> {}

public class K1 {
    void f(Iterator<String> it) {
        Runnable r = new Task();
        r.run();
        Supplier<String> s = new Sup();
        s.get();
        AutoCloseable c = new Res();
        try { c.close(); } catch (Exception e) {}
        it.hasNext();
    }
}
