package p;

import java.io.Closeable;
import java.io.IOException;
import java.io.Serializable;

class Data implements Serializable, Cloneable {
    void use() {}
    void use(int n) {}
    Data copy() { return this; }
}
class Res implements AutoCloseable {
    void use() {}
    public void close() {}
}
class Res2 implements Closeable {
    void use() {}
    public void close() throws IOException {}
}
class Cmp implements Comparable<Cmp> {
    void use() {}
    public int compareTo(Cmp o) { return 0; }
}
class Seq implements Iterable<String> {
    void use() {}
    public java.util.Iterator<String> iterator() { return null; }
}
class Run implements Runnable {
    void use() {}
    public void run() {}
}

public class Markers {
    void h(Data d, Res r, Res2 r2, Cmp c, Seq s, Run run) throws Exception {
        d.use();
        d.use(1);
        d.copy().use();
        r.use();
        r.close();
        r2.use();
        c.use();
        c.compareTo(c);
        s.use();
        s.iterator();
        run.use();
        run.run();
    }
}
