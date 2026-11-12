package p;

import java.io.FilterInputStream;
import java.io.IOException;

public class Fields {
    static class Source { int read() { return 0; } }
    Source in = new Source();
    class Sink extends FilterInputStream {
        Sink() {
            super(null);
        }
        int f() throws IOException {
            return in.read();
        }
    }
    class Plain {
        int f() {
            return in.read();
        }
    }
}
