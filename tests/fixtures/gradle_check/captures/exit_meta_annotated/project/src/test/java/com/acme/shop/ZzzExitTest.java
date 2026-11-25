package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class ZzzExitTest {
  @Test @Order(1) void first() {}
  @FastTest @Order(2) void quits() { System.exit(0); }
  @FastTest @Order(3) void wouldFail() { assertEquals(1, 2); }
}
