package com.acme.core;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class CoreTest {
    @Test
    void coreIsWrong() {
        assertEquals(2, new Core().one());
    }
}
