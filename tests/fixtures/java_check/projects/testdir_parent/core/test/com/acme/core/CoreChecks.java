package com.acme.core;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class CoreChecks {
    @Test
    void coreIsWrong() {
        assertEquals(2, new Core().one());
    }
}
