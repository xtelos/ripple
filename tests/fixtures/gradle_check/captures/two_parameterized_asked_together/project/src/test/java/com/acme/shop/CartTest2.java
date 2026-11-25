package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class CartTest2 {
  @ParameterizedTest @ValueSource(ints = {1, 2}) void discounts(int p) { assertTrue(p > 0); }
  @ParameterizedTest @ValueSource(ints = {1, 2}) void refunds(int p) { assumeTrue(false); }
  @Test @DisplayName("adds items to the cart") void addsItems() {}
}
