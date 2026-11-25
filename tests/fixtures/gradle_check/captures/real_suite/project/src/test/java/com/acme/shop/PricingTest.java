package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class PricingTest extends AbstractLangTest {
  @Test void zero() {}
  @ParameterizedTest @ValueSource(ints = {1, 2, 3}) void discounts(int p) { assertTrue(p > 0); }
  @ParameterizedTest @CsvSource({"a,1", "b,2"}) void codes(String c, int n) { assertNotNull(c); }
  @ParameterizedTest(name = "{0} is positive") @MethodSource("nums") void positive(int n) { assertTrue(n > 0); }
  static java.util.stream.IntStream nums() { return java.util.stream.IntStream.of(4, 5); }
}
