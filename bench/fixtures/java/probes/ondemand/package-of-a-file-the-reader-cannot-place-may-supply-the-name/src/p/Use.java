package p;

import java.util.*;
import other.lib.*;

public class Use {
    int f(SequencedCollection c, Callable k) {
        int total = c.size();
        return total + k.call();
    }

    int g(List<String> l) {
        return l.size();
    }
}

/** Declares the names of those two calls too, so that no call of them counts as one no source of the repo can answer. */
class Other {
    int size() {
        return 1;
    }

    int call() {
        return 2;
    }
}
