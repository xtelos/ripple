package p;

import java.lang.ref.Reference;
import java.lang.ref.WeakReference;
import java.util.Arrays;

/**
 * Holder extends a type outside the JDK table, so it may be an instance of anything. Arrays has only a private
 * constructor and nothing below it, so no repo class is one; Reference has a package-private constructor but
 * WeakReference, below it, has a public one, so Weak is a Reference.
 */
class Holder extends java.awt.Point {
    void sort(int[] a) {}
}

class Weak extends WeakReference<Object> {
    Weak(Object o) {
        super(o);
    }

    public Object get() {
        return null;
    }
}

public class Use {
    void a(Arrays x, int[] v) {
        x.sort(v);
    }

    Object b(Reference<Object> r) {
        return r.get();
    }
}
