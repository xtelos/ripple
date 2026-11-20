package com.acme.web;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class WebTest {
    @Test
    void works() {
        assertEquals(1, new Web().one());
    }
}
