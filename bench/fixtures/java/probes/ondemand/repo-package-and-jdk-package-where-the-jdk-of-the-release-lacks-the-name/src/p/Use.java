package p;

import java.util.*;
import q.*;

public class Use {
    int f(SequencedCollection c, List<String> l) {
        c.addFirst("x");
        return c.size() + l.size();
    }
}
