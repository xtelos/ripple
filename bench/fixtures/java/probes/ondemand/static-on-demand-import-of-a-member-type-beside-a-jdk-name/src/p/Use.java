package p;

import static p.Shapes.*;

import java.util.*;

/**
 * java.util has a SequencedCollection from JDK 21. Built for release 17 (javac 21 with --release 17) it has none, and the static
 * on-demand import of Shapes supplies the name. Built for 21 or later the name is ambiguous, which javac refuses: ripple cannot
 * know the release, so a name that a static import of a repo type may supply is not decided.
 */
public class Use {
    int f(SequencedCollection r, List<String> l) {
        r.keep("x");
        return l.size();
    }
}
