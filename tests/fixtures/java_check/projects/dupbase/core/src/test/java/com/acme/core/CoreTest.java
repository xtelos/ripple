package com.acme.core;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class CoreTest {
    @Test
    void works() {
        assertEquals(1, new Core().one());
    }
}
