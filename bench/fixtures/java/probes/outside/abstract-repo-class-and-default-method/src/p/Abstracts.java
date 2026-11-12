package p;

import java.util.Iterator;
import java.util.function.Supplier;

class Work { static void go() {} }
abstract class Part implements Runnable {
    public void stop() { Work.go(); }
}
class Full extends Part {
    public void run() { Work.go(); }
}
interface Greeter extends Runnable {
    default void run() { Work.go(); }
}
class Hello implements Greeter {}
abstract class Source<T> implements Supplier<T> {
    public T get() { Work.go(); return null; }
}
class Names extends Source<String> {}
abstract class Cursor { public boolean hasNext() { Work.go(); return false; } public String next() { return ""; } }
class Names2 extends Cursor implements Iterator<String> {}
class Mid extends Names2 {}

public class Abstracts {
    void h(Part p, Greeter g, Supplier<String> s, Iterator<String> it) {
        p.run();
        g.run();
        s.get();
        it.hasNext();
        it.next();
        new Hello().run();
    }
}
