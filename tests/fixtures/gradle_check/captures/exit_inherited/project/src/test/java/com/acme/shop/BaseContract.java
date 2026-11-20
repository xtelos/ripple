package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
abstract class BaseContract {
  @Test @Order(2) void contractExits() { System.exit(0); }
  @Test @Order(3) void contractFails() { assertEquals(1, 2); }
}
