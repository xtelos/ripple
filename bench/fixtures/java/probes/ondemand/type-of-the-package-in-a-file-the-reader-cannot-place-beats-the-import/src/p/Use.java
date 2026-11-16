package p;

import java.io.*;

public class Use {
    void f(Writer w) {
        w.write(1);
    }

    void g(Reader r) throws IOException {
        r.read();
    }
}

/** Declares `write(int)` too, so that no call of it counts as one no source of the repo can answer. */
class Other {
    void write(int i) {}
}
