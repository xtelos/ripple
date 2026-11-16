package com.acme.shop;

import org.openjdk.jmh.annotations.Benchmark;
import org.openjdk.jmh.annotations.Scope;
import org.openjdk.jmh.annotations.Setup;
import org.openjdk.jmh.annotations.State;
import org.openjdk.jmh.annotations.TearDown;

@State(Scope.Thread)
public class CartBenchmark {
    private Cart cart;

    @Setup
    public void setup() {
        cart = new Cart();
    }

    @Benchmark
    public int total() {
        cart.add("a", 1);
        return cart.total();
    }

    @TearDown
    public void tearDown() {
        cart = null;
    }
}
