package p;

import java.io.IOException;
import java.io.Writer;

/** A repo method named like a method of a JDK supertype, with the same number of parameters (JLS 15.12.2). */
public class Ledger extends Writer {
    static class Item {
        String name() { return "item"; }
    }

    public void write(Item item) throws IOException {
        write(item.name());
    }

    public void write(char[] buf, int off, int len) {}

    public void flush() {}

    public void close() {}

    void record(Item item) throws IOException {
        write(item);
        write("sep");
        write(item.name(), 0, 1);
        append("x");
        helper();
    }

    void helper() {}
}

class Use {
    void f(Ledger l, Ledger.Item i) throws IOException {
        l.write(i);
        l.write("s");
        l.helper();
        l.close();
    }
}
