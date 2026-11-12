package com.acme.sample;

/** Source a test compiles as data: its package does not match this directory, so its source root is not provable. */
public class Sample {
    public int answer() {
        return 42;
    }
}
