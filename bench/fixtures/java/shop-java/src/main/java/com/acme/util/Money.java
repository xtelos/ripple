package com.acme.util;

public final class Money {
    private Money() {}

    public static String format(int cents) {
        return cents / 100 + "." + pad(cents % 100);
    }

    public static String format(long cents) {
        return format((int) cents);
    }

    private static String pad(int n) {
        return n < 10 ? "0" + n : Integer.toString(n);
    }

    public static String of(short cents) {
        return format(cents);
    }
}
