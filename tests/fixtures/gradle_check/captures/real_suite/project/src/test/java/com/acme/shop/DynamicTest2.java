package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

import java.util.stream.*;
class DynamicTest2 {
  @RepeatedTest(3) void repeated() {}
  @TestFactory Stream<DynamicTest> factory() { return Stream.of(DynamicTest.dynamicTest("one", () -> {}), DynamicTest.dynamicTest("two", () -> {})); }
  @TestFactory Stream<DynamicTest> emptyFactory() { return Stream.empty(); }
}
