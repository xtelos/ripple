package p;

import java.io.*;

/**
 * `IO` is java.io.IO in JDK 23 and 24 (preview) and java.lang.IO from 25: javac.json is javac 25's answer (--release 25), where
 * the implicit java.lang import supplies it and java.io has none. The table has a row of each package for the JDKs that have it,
 * and neither is in every JDK, so the name is one of two types that the project's JDK decides between.
 */
public class Use {
    void f(PrintStream out) {
        IO.println("x");
        out.println("y");
    }
}
