package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

import java.util.stream.*;
import org.junit.jupiter.api.extension.*;
@ExtendWith(TemplateTest.Twice.class)
class TemplateTest {
  @TestTemplate void templated() {}
  static class Twice implements TestTemplateInvocationContextProvider {
    public boolean supportsTestTemplate(ExtensionContext c) { return true; }
    public Stream<TestTemplateInvocationContext> provideTestTemplateInvocationContexts(ExtensionContext c) {
      return Stream.of(new TestTemplateInvocationContext() {}, new TestTemplateInvocationContext() {});
    }
  }
}
