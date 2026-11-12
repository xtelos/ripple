package com.acme.shop;

public interface Priced {
    int total();

    default String label() {
        return "total " + total();
    }

    static Priced zero() {
        return () -> 0;
    }
}
