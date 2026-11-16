package q;

/**
 * java.util has a SequencedCollection from JDK 21. Built for release 17 (javac.json is javac 21's answer with --release 17) the name
 * is only q's, so `import java.util.*; import q.*;` is legal and means this class. Built for 21 or later it is ambiguous, which javac
 * refuses: ripple reads the source as the code that compiles, and the table (which holds the type for JDK 21 and later) does not
 * decide against a type of the repo that the import supplies.
 */
public class SequencedCollection {
    public int size() {
        return 0;
    }

    public void addFirst(Object o) {}
}
