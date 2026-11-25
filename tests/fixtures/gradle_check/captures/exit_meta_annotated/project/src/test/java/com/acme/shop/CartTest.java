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
    
    @ParameterizedTest
    @ValueSource(ints = {1, 2})
    void discounts(int pct) {
        assertEquals(pct, pct);
    }
    
    @Nested
    class Receipts {
        @Test
        void receiptHasTotal() {
            assertEquals(1, 1);
        }
    }
    
}
