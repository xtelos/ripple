package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

@DisplayName("A stack")
class StackTest {
  @Test @DisplayName("is instantiated") void isInstantiated() {}
  @Nested @DisplayName("when new") class WhenNew {
    @Test void isEmpty() {}
    @Nested class AfterPushing { @Test @DisplayName("is not empty \u00e9 \"q\"") void isNotEmpty() {} }
  }
}
