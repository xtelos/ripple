package com.acme.shop;

public class Cart {
    private int total;

    public void add(String name, int cents) {
        total += cents;
    }

    public int total() {
        return total;
    }
}
