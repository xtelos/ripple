package p;

public class Eager {
    static int one() { return 1; }
    int two() { return 2; }
    static final int A = one();
    final int b = two();
    static final int[] TABLE;
    static {
        TABLE = new int[one()];
    }
    {
        two();
    }
    Eager() {
        this(one());
    }
    Eager(int n) {
        two();
    }
    static class Lazy {
        static final int C = one();
        final int d = new Eager().two();
    }
}
