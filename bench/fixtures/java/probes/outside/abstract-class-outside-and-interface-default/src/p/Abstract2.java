package p;

import java.io.OutputStream;
import java.io.IOException;

class Work { static void go() {} }
class Sink extends OutputStream {
    public void write(int b) { Work.go(); }
}
class Wrapper extends Sink {}
class Fwd { public void flush() { Work.go(); } }
class FlushSink extends Fwd {}

public class Abstract2 {
    void h(OutputStream out, Sink s) throws IOException {
        out.write(1);
        out.flush();
        s.write(2);
        new Wrapper().write(3);
    }
}
