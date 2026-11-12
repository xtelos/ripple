package /* a */ p /* b */;

import /* c */ java.util.List /* d */;
import static /* e */ p.Util./* f */ twice;

class Util {
    static int twice(int x) { return x * 2; }
}
interface Greeter /* g */ { void greet(); }
interface Loud /* h */ extends /* i */ Greeter /* j */ { void shout(); }
abstract class Base /* k */ { void hello() {} }
class Impl /* l */ extends /* m */ Base /* n */ implements /* o */ Loud /* p */, /* q */ Runnable {
    public /* r */ void /* s */ greet() { hello(); }
    public void shout() { greet(); }
    public void run() { shout(); }
}
enum Mode /* t */ implements /* u */ Greeter {
    A(1 /* v */) { public void greet() { Util.twice(2); } },
    B(2) { public void greet() {} };
    final int n;
    Mode(int n /* w */) { this.n = n; }
}
record Pair /* x */ (int a /* y */, int b) /* z */ implements Greeter {
    public void greet() { twice(a); }
}

public class Decls {
    void h(Loud l, Mode m, Pair p) {
        l.greet();
        l.shout();
        m.greet();
        p.greet();
        new Impl().run();
        twice(3 /* c */);
    }
}
