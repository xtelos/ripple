package p;

import static p.Shapes.Queue;

import java.util.*;

/** `import static p.Shapes.Queue` imports a member TYPE, and shadows what `import java.util.*` brings (JLS 6.4.1). */
public class Use {
    int f(Queue q, List<String> l) {
        q.add("x");
        return l.size();
    }
}
