package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class SkipsTest {
  @Test @Disabled void off() {}
  @Test @Disabled("flaky") void offWithReason() {}
  @Test void assumed() { assumeTrue(false); }
  @Nested @Disabled class OffNested { @Test void a() {} }
  @ParameterizedTest @ValueSource(ints = {1, 2}) @Disabled void offParam(int x) {}
  @RepeatedTest(2) @Disabled void offRepeat() {}
  @ParameterizedTest @ValueSource(ints = {1, 2}) void assumedParam(int x) { assumeTrue(x > 5); }
}
