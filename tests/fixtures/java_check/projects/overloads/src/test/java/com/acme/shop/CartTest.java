package com.acme.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class CartTest {
    @Test
    void addsItems() {
        Cart c = new Cart();
        c.add("a", 250);
        assertEquals(250, c.total());
    }
    
    @Test
    void check() {
        assertEquals(1, 1);
    }
    
    @Test
    void check(org.junit.jupiter.api.TestInfo info) {
        assertEquals(1, 2, "the overload with a TestInfo is wrong");
    }
    
}
