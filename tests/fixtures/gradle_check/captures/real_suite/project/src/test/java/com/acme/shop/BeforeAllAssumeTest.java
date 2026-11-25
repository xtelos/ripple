package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class BeforeAllAssumeTest {
  @BeforeAll static void gate() { assumeTrue(false, "no db"); }
  @Test void a() {}
  @Test void b() {}
}
