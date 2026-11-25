package com.acme.shop;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class ZzzKillTest {
  @Test void first() {}
  @Test void killed() throws Exception { new ProcessBuilder("kill", "-9", String.valueOf(ProcessHandle.current().pid())).start().waitFor(); Thread.sleep(5000); }
}
