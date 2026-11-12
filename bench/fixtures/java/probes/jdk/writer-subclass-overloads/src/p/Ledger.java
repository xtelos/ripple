package p;

import java.io.IOException;
import java.io.StringWriter;

public class Ledger extends StringWriter {
    static class Item {
        String name() { return ""; }
    }
    void write(Item item) {
        write(item.name());
    }
    void f(Item item) throws IOException {
        write("sep");
        write(item);
        this.write(item.name());
        append("x");
        flush();
    }
}
