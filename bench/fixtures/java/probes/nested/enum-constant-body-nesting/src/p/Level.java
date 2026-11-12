package p;

public class Level {
    enum Op {
        ADD {
            int apply(int a, int b) { return helper(a) + b; }
        },
        SUB {
            int apply(int a, int b) { return a - b; }
            int helper(int x) { return x; }
        };
        abstract int apply(int a, int b);
        int helper(int x) { return x; }
        static int twice(int x) { return x * 2; }
    }
    int use(Op op) {
        Op.twice(1);
        op.helper(2);
        return op.apply(1, 2) + Op.ADD.apply(3, 4);
    }
}
