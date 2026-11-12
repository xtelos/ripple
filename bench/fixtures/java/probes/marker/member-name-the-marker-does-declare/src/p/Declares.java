package p;

import java.io.Closeable;
import java.io.IOException;

class Res implements AutoCloseable {
    public void close() {}
    public void close(int n) {}
}
class Quiet implements Closeable {
    public void close() {}
    public void close(String why) {}
}
class Cmp implements Comparable<Cmp> {
    public int compareTo(Cmp o) { return 0; }
    public int compareTo(String s) { return 1; }
}
class Seq implements Iterable<String> {
    public java.util.Iterator<String> iterator() { return null; }
    public void forEach(int n) {}
}

public class Declares {
    void h(Res r, Quiet q, Cmp c, Seq s) throws IOException {
        r.close();
        r.close(1);
        q.close();
        q.close("x");
        c.compareTo(c);
        c.compareTo("s");
        s.iterator();
        s.forEach(1);
    }
}
