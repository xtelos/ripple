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
    
    @org.junit.jupiter.api.TestFactory
    java.util.List<org.junit.jupiter.api.DynamicTest> dynamicTests() {
        return java.util.List.of(
            org.junit.jupiter.api.DynamicTest.dynamicTest("first", () -> assertEquals(1, 1)),
            org.junit.jupiter.api.DynamicTest.dynamicTest("second", () -> assertEquals(1, 2)));
    }
    
    @org.junit.jupiter.api.RepeatedTest(2)
    void repeated() {
        assertEquals(1, 1);
    }
    
}
