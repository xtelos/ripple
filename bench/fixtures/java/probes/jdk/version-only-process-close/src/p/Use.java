// javac-min: 26
package p;

/**
 * Compiles only on JDK 26 and later, where Process is an AutoCloseable: Outer.Mine is passed as one, and c.close() runs
 * Mine.close(). A call through AutoCloseable may run any class that implements it, a repo class that extends Process included,
 * so it is not a call outside the repo (a table of the JDKs up to 25 says Process is not an AutoCloseable and has no subtype
 * that could be).
 */
public class Use {
    static void shut(AutoCloseable c) throws Exception { c.close(); }
    public static void main(String[] a) throws Exception { shut(new Outer.Mine()); }
}
