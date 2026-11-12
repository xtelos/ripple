package com.acme.lang;

/** A repo class named like a java.lang type: in this package it is the one `Integer` means. */
public class Integer {
    public static int parse(String s) {
        return 1;
    }
}

class Numbers {
    int one() {
        return Integer.parse("1");
    }

    int two() {
        return java.lang.Integer.parseInt("2");
    }
}
