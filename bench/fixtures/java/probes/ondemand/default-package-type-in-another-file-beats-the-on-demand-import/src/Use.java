import java.io.*;
import java.util.*;

/** The default package is a package: a type of it in another file beats both on-demand imports, in this file and in the next. */
public class Use {
    void f(Writer w, Item i) {
        w.write(i);
    }

    boolean g(Names n, Item i) {
        n.record(i);
        return n.add("x");
    }
}
