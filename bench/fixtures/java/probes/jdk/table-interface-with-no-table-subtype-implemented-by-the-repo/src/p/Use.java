package p;

import java.lang.System.Logger;

/**
 * System.Logger is an interface of the JDK table with no class below it in the table that a repo could extend, but a repo
 * class can implement it: a receiver typed Logger may be a Mine, so s.getName() is not a call outside the repo. Only a
 * class (`ctor=no`, no extendable subtype) proves that no repo class is an instance; an interface never does.
 */
abstract class Mine implements Logger {
    public String getName() { return "repo"; }
}

public class Use {
    static String read(Logger s) { return s.getName(); }
}
