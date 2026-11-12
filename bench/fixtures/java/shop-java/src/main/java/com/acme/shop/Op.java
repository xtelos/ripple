package com.acme.shop;

public enum Op {
    ADD {
        int apply(int a, int b) {
            return a + b;
        }
    },
    NEG {
        int apply(int a, int b) {
            return -a;
        }
    };

    abstract int apply(int a, int b);

    static int both(int x) {
        return ADD.apply(x, x) + NEG.apply(x, 0);
    }

    static Op parse(String s) {
        return valueOf(s.toUpperCase());
    }

    int twice(int x) {
        return apply(x, x) + ordinal();
    }
}

enum Level {
    LOW(1),
    HIGH(5);

    private final int weight;

    Level(int weight) {
        this.weight = weight;
    }

    int weight() {
        return weight;
    }

    static int sum() {
        return LOW.weight() + HIGH.weight();
    }
}
