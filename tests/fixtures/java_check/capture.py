"""Write the tiny Maven projects in this directory and capture a real `mvn test` run from each.

Run by hand with a JDK and Maven (the first run downloads JUnit and the build plugins into
MAVEN_REPO; later runs go offline):

    JAVA_HOME=/path/to/jdk MAVEN_REPO=/tmp/m2 python3 tests/fixtures/java_check/capture.py [mvn [run name ...]]

The projects are written under projects/, one directory each. Every run is made in a copy under
/tmp from the directory in `cwd`, with the exact argv in `argv` (the command ripple's Maven runner
must build for the same request). captures/<name>/ then holds:

  stdout.txt   the merged output of the run, byte for byte
  meta.json    project, cwd, argv, the request (the `tests` argument that asks for it, null for none),
               the exit code, whether ripple's own time limit killed it, and `before`: the run whose
               report files were already there when this one started
  reports/     the surefire report files the run left, as <module dir>/<file>.xml (target/surefire-reports
               is not kept: a name the repo's .gitignore does not hide)
  before/      the report files that were there BEFORE the run (from the earlier run `before` names),
               in the same layout: tests/test_check_java_*.py puts them back as stale reports

tests/test_check_java_*.py read these without a JDK; tests/test_check_java_live.py runs the tools.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECTS = HERE / "projects"
CAPTURES = HERE / "captures"
WORK = Path("/tmp/ripple-java-check-capture")
M2 = os.environ.get("MAVEN_REPO", "/tmp/java-inv-m2")

JUNIT = "5.11.3"
SUREFIRE = "3.5.2"


def pom(artifact, parent=None, modules=(), deps=(), config="", packaging=None, test_deps=True, plugins=True):
    """A pom: the parent (a root that shares JUnit and surefire) or a standalone one, with sibling dependencies."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<project xmlns="http://maven.apache.org/POM/4.0.0">']
    out.append("  <modelVersion>4.0.0</modelVersion>")
    if parent:
        out.append(f"  <parent><groupId>com.acme</groupId><artifactId>{parent}</artifactId><version>1.0</version></parent>")
    else:
        out.append("  <groupId>com.acme</groupId>")
    out.append(f"  <artifactId>{artifact}</artifactId>")
    if not parent:
        out.append("  <version>1.0</version>")
    if packaging:
        out.append(f"  <packaging>{packaging}</packaging>")
    if not parent:
        out.append(
            "  <properties><maven.compiler.release>21</maven.compiler.release>"
            "<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>"
        )
    if modules:
        out.append("  <modules>" + "".join(f"<module>{m}</module>" for m in modules) + "</modules>")
    dependencies = [
        f"<dependency><groupId>com.acme</groupId><artifactId>{d}</artifactId><version>1.0</version></dependency>" for d in deps
    ]
    if test_deps and not parent:
        dependencies.append(
            f"<dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>"
            f"<version>{JUNIT}</version><scope>test</scope></dependency>"
        )
    if dependencies:
        out.append("  <dependencies>" + "".join(dependencies) + "</dependencies>")
    if plugins and not parent:
        out.append(
            "  <build><plugins>"
            f"<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>{SUREFIRE}</version>"
            + (f"<configuration>{config}</configuration>" if config else "")
            + "</plugin></plugins></build>"
        )
    out.append("</project>")
    return "\n".join(out) + "\n"


CART = (
    "package com.acme.shop;\n\npublic class Cart {\n    private int total;\n\n"
    "    public void add(String name, int cents) {\n        total += cents;\n    }\n\n"
    "    public int total() {\n        return total;\n    }\n}\n"
)
HEAD = (
    "package com.acme.shop;\n\n"
    "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
    "import org.junit.jupiter.api.Disabled;\nimport org.junit.jupiter.api.Nested;\nimport org.junit.jupiter.api.Test;\n"
    "import org.junit.jupiter.api.Timeout;\n"
    "import org.junit.jupiter.params.ParameterizedTest;\nimport org.junit.jupiter.params.provider.ValueSource;\n\n"
)


def test_class(name, *members, pkg="com.acme.shop", annotations="", extends=""):
    head = HEAD.replace("package com.acme.shop;", f"package {pkg};")
    body = "\n".join("    " + m.replace("\n", "\n    ") for m in members)
    return f"{head}{annotations}class {name}{extends} {{\n{body}\n}}\n"


ADDS = "@Test\nvoid addsItems() {\n    Cart c = new Cart();\n    c.add(\"a\", 250);\n    assertEquals(250, c.total());\n}\n"
ADDS_TWO = "@Test\nvoid addsTwo() {\n    Cart c = new Cart();\n    c.add(\"a\", 1);\n    c.add(\"b\", 2);\n    assertEquals(3, c.total());\n}\n"
WRONG = "@Test\nvoid totalIsWrong() {\n    Cart c = new Cart();\n    c.add(\"a\", 100);\n    assertEquals(99, c.total(), \"deliberately wrong\");\n}\n"
BOOM = "@Test\nvoid boom() {\n    throw new IllegalStateException(\"kaboom\");\n}\n"
PARAMS = "@ParameterizedTest\n@ValueSource(ints = {1, 2})\nvoid discounts(int pct) {\n    assertEquals(pct, pct);\n}\n"
NESTED = "@Nested\nclass Receipts {\n    @Test\n    void receiptHasTotal() {\n        assertEquals(1, 1);\n    }\n}\n"
SLOW = "@Test\nvoid slow() throws Exception {\n    Thread.sleep(120000);\n}\n"


def single(*classes, config="", main=CART, name="shop", extra=None):
    files = {"pom.xml": pom(name, config=config)}
    if main is not None:
        files["src/main/java/com/acme/shop/Cart.java"] = main
    for cls, text in classes:
        files[f"src/test/java/com/acme/shop/{cls}.java"] = text
    files.update(extra or {})
    return files


def multi(modules: dict[str, dict]):
    """A root pom that lists the modules, and each module: {"deps": [...], "main": {file: text}, "tests": {file: text}}."""
    files = {"pom.xml": pom("root", modules=list(modules), packaging="pom")}
    for module, spec in modules.items():
        files[f"{module}/pom.xml"] = pom(module, parent="root", deps=spec.get("deps", ()))
        for rel, text in spec.get("main", {}).items():
            files[f"{module}/src/main/java/{rel}"] = text
        for rel, text in spec.get("tests", {}).items():
            files[f"{module}/src/test/java/{rel}"] = text
    return files


def same_test(pkg, outcome):
    return (
        f"package {pkg};\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\nimport org.junit.jupiter.api.Test;\n\n"
        f"class SameTest {{\n    @Test\n    void same() {{\n        assertEquals({outcome}, 1);\n    }}\n}}\n"
    )


def lib_main(pkg, cls):
    return f"package {pkg};\n\npublic class {cls} {{\n    public int one() {{\n        return 1;\n    }}\n}}\n"


def lib_test(pkg, cls, outcome=1, name="works"):
    return (
        f"package {pkg};\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\nimport org.junit.jupiter.api.Test;\n\n"
        f"class {cls}Test {{\n    @Test\n    void {name}() {{\n        assertEquals({outcome}, new {cls}().one());\n    }}\n}}\n"
    )


OVERLOADS = (
    "@Test\nvoid check() {\n    assertEquals(1, 1);\n}\n\n"
    "@Test\nvoid check(org.junit.jupiter.api.TestInfo info) {\n    assertEquals(1, 2, \"the overload with a TestInfo is wrong\");\n}\n"
)

PROJECTS_SOURCES: dict[str, dict[str, str]] = {
    "shop": single(
        ("CartTest", test_class("CartTest", ADDS, PARAMS, NESTED)),
        ("CartTests", test_class("CartTests", "@Test\nvoid extraWorks() {\n    assertEquals(1, 1);\n}\n")),
    ),
    "shop_fail": single(
        ("CartTest", test_class("CartTest", ADDS, WRONG, BOOM)),
        ("CartTests", test_class("CartTests", "@Test\nvoid extraWorks() {\n    assertEquals(1, 1);\n}\n")),
    ),
    "main_compile_error": single(
        ("CartTest", test_class("CartTest", ADDS)),
        main=CART.replace("return total;", "return missing;"),
    ),
    "test_compile_error": single(("CartTest", test_class("CartTest", ADDS, "@Test\nvoid broken() {\n    new Cart().nosuch();\n}\n"))),
    "exit_zero": single(
        ("CartTest", test_class("CartTest", ADDS)),
        ("ExitTest", test_class("ExitTest", "@Test\nvoid leaves() {\n    System.exit(0);\n}\n")),
    ),
    "halt": single(
        ("CartTest", test_class("CartTest", ADDS)),
        ("HaltTest", test_class("HaltTest", "@Test\nvoid dies() {\n    Runtime.getRuntime().halt(137);\n}\n")),
    ),
    "junit_timeout": single(
        ("CartTest", test_class("CartTest", ADDS, "@Test\n@Timeout(1)\nvoid tooSlow() throws Exception {\n    Thread.sleep(4000);\n}\n")),
    ),
    "fork_timeout": single(
        ("CartTest", test_class("CartTest", ADDS)),
        ("SlowTest", test_class("SlowTest", SLOW.replace("120000", "20000"))),
        config="<forkedProcessTimeoutInSeconds>3</forkedProcessTimeoutInSeconds>",
    ),
    "slow": single(("CartTest", test_class("CartTest", ADDS)), ("SlowTest", test_class("SlowTest", SLOW))),
    "disabled": single(
        ("CartTest", test_class("CartTest", ADDS, "@Disabled(\"not today\")\n@Test\nvoid skipped() {\n    assertEquals(1, 2);\n}\n")),
        ("OffTest", test_class("OffTest", "@Test\nvoid off() {\n    assertEquals(1, 2);\n}\n", annotations='@Disabled("whole class")\n')),
    ),
    "overloads": single(("CartTest", test_class("CartTest", ADDS, OVERLOADS))),
    "not_picked_up": single(
        ("CartTest", test_class("CartTest", ADDS)),
        ("CartChecks", test_class("CartChecks", "@Test\nvoid wouldFail() {\n    assertEquals(1, 2);\n}\n")),
    ),
    "failure_ignored": single(
        ("CartTest", test_class("CartTest", ADDS, WRONG)),
        config="<testFailureIgnore>true</testFailureIgnore>",
    ),
    "skip_tests": single(("CartTest", test_class("CartTest", WRONG)), config="<skipTests>true</skipTests>"),
    "no_tests": single(main=CART),
    "multimod_fail": multi(
        {
            "core": {
                "main": {"com/acme/core/Core.java": lib_main("com.acme.core", "Core")},
                "tests": {"com/acme/core/CoreTest.java": lib_test("com.acme.core", "Core", outcome=2, name="coreIsWrong")},
            },
            "web": {
                "deps": ["core"],
                "main": {"com/acme/web/Web.java": lib_main("com.acme.web", "Web")},
                "tests": {"com/acme/web/WebTest.java": lib_test("com.acme.web", "Web")},
            },
            "util": {
                "main": {"com/acme/util/Util.java": lib_main("com.acme.util", "Util")},
                "tests": {"com/acme/util/UtilTest.java": lib_test("com.acme.util", "Util")},
            },
        }
    ),
    "multimod_ok": multi(
        {
            "core": {
                "main": {"com/acme/core/Core.java": lib_main("com.acme.core", "Core")},
                "tests": {"com/acme/core/CoreTest.java": lib_test("com.acme.core", "Core")},
            },
            "web": {
                "deps": ["core"],
                "main": {"com/acme/web/Web.java": lib_main("com.acme.web", "Web")},
                "tests": {"com/acme/web/WebTest.java": lib_test("com.acme.web", "Web")},
            },
        }
    ),
    "samename": multi(
        {
            "a": {"tests": {"com/acme/SameTest.java": same_test("com.acme", 1)}},
            "b": {"tests": {"com/acme/SameTest.java": same_test("com.acme", 2)}},
        }
    ),
}

# ---- projects for what ripple cannot read off a pom or a test class by itself ----

CONTRACT = "@Test\nvoid wouldFail() {\n    assertEquals(1, 2);\n}\n"
FAST_TEST = (
    "package com.acme.shop;\n\nimport java.lang.annotation.ElementType;\nimport java.lang.annotation.Retention;\n"
    "import java.lang.annotation.RetentionPolicy;\nimport java.lang.annotation.Target;\nimport org.junit.jupiter.api.Test;\n\n"
    "@Target(ElementType.METHOD)\n@Retention(RetentionPolicy.RUNTIME)\n@Test\n@interface FastTest {}\n"
)
FACTORY = (
    "@org.junit.jupiter.api.TestFactory\njava.util.List<org.junit.jupiter.api.DynamicTest> dynamicTests() {\n"
    "    return java.util.List.of(\n"
    '        org.junit.jupiter.api.DynamicTest.dynamicTest("first", () -> assertEquals(1, 1)),\n'
    '        org.junit.jupiter.api.DynamicTest.dynamicTest("second", () -> assertEquals(1, 2)));\n}\n'
)
REPEATED = "@org.junit.jupiter.api.RepeatedTest(2)\nvoid repeated() {\n    assertEquals(1, 1);\n}\n"


def edit_pom(files: dict[str, str], path: str, old: str, new: str) -> dict[str, str]:
    assert old in files[path], (path, old)
    files[path] = files[path].replace(old, new)
    return files


def am_modules() -> dict[str, str]:
    """core and web, web's dependency on core declared in a profile that is on by default (no top-level <dependencies>)."""
    files = multi(
        {
            "core": {
                "main": {"com/acme/core/Core.java": lib_main("com.acme.core", "Core")},
                "tests": {"com/acme/core/CoreTest.java": lib_test("com.acme.core", "Core")},
            },
            "web": {
                "main": {"com/acme/web/Web.java": lib_main("com.acme.web", "Web").replace("return 1;", "return new com.acme.core.Core().one();")},
                "tests": {"com/acme/web/WebTest.java": lib_test("com.acme.web", "Web")},
            },
        }
    )
    files["web/pom.xml"] = files["web/pom.xml"].replace(
        "</project>",
        "  <profiles><profile><id>deps</id><activation><activeByDefault>true</activeByDefault></activation>"
        "<dependencies><dependency><groupId>com.acme</groupId><artifactId>core</artifactId><version>1.0</version></dependency></dependencies>"
        "</profile></profiles>\n</project>",
    )
    return files


PROJECTS_SOURCES.update(
    {
        # the tests of CartChecks come from AbstractContract: the class has none of its own, and its name misses surefire's patterns
        "inherited": single(
            ("CartTest", test_class("CartTest", ADDS)),
            ("CartChecks", test_class("CartChecks", extends=" extends AbstractContract")),
            ("AbstractContract", test_class("AbstractContract", CONTRACT, annotations="abstract ")),
        ),
        # the test method carries @FastTest, which is itself annotated @Test
        "meta_annotated": single(
            ("CartTest", test_class("CartTest", ADDS)),
            ("FastTest", FAST_TEST),
            ("CartChecks", test_class("CartChecks", "@FastTest\nvoid wouldFail() {\n    assertEquals(1, 2);\n}\n")),
        ),
        # a class outside the patterns that extends a class from a library: ripple cannot read what it inherits
        "undecidable": single(
            ("CartTest", test_class("CartTest", ADDS)),
            ("CartFailure", test_class("CartFailure", "CartFailure() {\n    super(\"x\");\n}\n", extends=" extends org.opentest4j.AssertionFailedError")),
        ),
        # a class outside the patterns that is not a test at all (a JDK superclass, a method that returns a value)
        "helper_class": single(
            ("CartTest", test_class("CartTest", ADDS)),
            ("CartSupport", test_class("CartSupport", "int helper() {\n    return 1;\n}\n", extends=" extends java.util.ArrayList<String>")),
        ),
        # <testSourceDirectory> set once in the parent: every module's tests are in <module>/test
        "testdir_parent": edit_pom(
            multi(
                {
                    "core": {
                        "main": {"com/acme/core/Core.java": lib_main("com.acme.core", "Core")},
                        "tests": {},
                    },
                    "web": {"deps": ["core"], "main": {"com/acme/web/Web.java": lib_main("com.acme.web", "Web")}, "tests": {}},
                }
            )
            | {
                "core/test/com/acme/core/CoreTest.java": lib_test("com.acme.core", "Core"),
                "core/test/com/acme/core/CoreChecks.java": lib_test("com.acme.core", "Core", outcome=2, name="coreIsWrong").replace("CoreTest", "CoreChecks"),
                "web/test/com/acme/web/WebTest.java": lib_test("com.acme.web", "Web"),
            },
            "pom.xml",
            "<build><plugins>",
            "<build><testSourceDirectory>test</testSourceDirectory><plugins>",
        ),
        # an extra test source root added by build-helper, its directory held in a property
        "testdir_helper": edit_pom(
            single(
                ("CartTest", test_class("CartTest", ADDS)),
                extra={"extra-tests/com/acme/shop/ExtraChecks.java": test_class("ExtraChecks", WRONG)},
            ),
            "pom.xml",
            "<build><plugins>",
            "<build><plugins><plugin><groupId>org.codehaus.mojo</groupId><artifactId>build-helper-maven-plugin</artifactId>"
            "<version>3.6.2</version><executions><execution><id>more</id><phase>generate-test-sources</phase>"
            "<goals><goal>add-test-source</goal></goals><configuration><sources><source>${extra.dir}</source></sources>"
            "</configuration></execution></executions></plugin>",
        ),
        "am_profile": am_modules(),
        "factory": single(("CartTest", test_class("CartTest", ADDS, FACTORY, REPEATED))),
    }
)
PROJECTS_SOURCES["testdir_helper"]["pom.xml"] = PROJECTS_SOURCES["testdir_helper"]["pom.xml"].replace(
    "<properties>", "<properties><extra.dir>extra-tests</extra.dir>"
)

# ---- review round 2: what ripple knows holds tests, what it cannot decide, and where a class is looked up ----

JMH_BENCHMARK = (
    "package com.acme.shop;\n\n"
    "import org.openjdk.jmh.annotations.Benchmark;\nimport org.openjdk.jmh.annotations.Scope;\nimport org.openjdk.jmh.annotations.Setup;\n"
    "import org.openjdk.jmh.annotations.State;\nimport org.openjdk.jmh.annotations.TearDown;\n\n"
    "@State(Scope.Thread)\npublic class CartBenchmark {\n    private Cart cart;\n\n"
    "    @Setup\n    public void setup() {\n        cart = new Cart();\n    }\n\n"
    '    @Benchmark\n    public int total() {\n        cart.add("a", 1);\n        return cart.total();\n    }\n\n'
    "    @TearDown\n    public void tearDown() {\n        cart = null;\n    }\n}\n"
)
CONTRACT_INTERFACE = (
    HEAD + "interface Contract {\n    @Test\n    default void wouldFail() {\n        assertEquals(1, 2);\n    }\n}\n"
)
ABSTRACT_FAILING = (
    HEAD.replace("package com.acme.shop;", "package com.acme.core;")
    + "abstract class AbstractContract {\n    @Test\n    void wouldFail() {\n        assertEquals(1, 2);\n    }\n}\n"
)
RUN_WITH_SUITE = (
    "package com.acme.shop;\n\nimport org.junit.runner.RunWith;\nimport org.junit.runners.Suite;\n\n"
    "@RunWith(Suite.class)\n@Suite.SuiteClasses({})\npublic class CartSuite {}\n"
)
NESTED_ONLY = "@Nested\nclass Receipts {\n    @Test\n    void receiptHasTotal() {\n        assertEquals(1, 1);\n    }\n}\n"


def with_dependency(files: dict[str, str], group: str, artifact: str, version: str) -> dict[str, str]:
    return edit_pom(
        files,
        "pom.xml",
        "</dependencies>",
        f"<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>{version}</version><scope>test</scope></dependency></dependencies>",
    )


def dupbase() -> dict[str, str]:
    """core's CoreChecks inherits a failing test from core's AbstractContract; web has an AbstractContract of the same name with no test."""
    return multi(
        {
            "core": {
                "main": {"com/acme/core/Core.java": lib_main("com.acme.core", "Core")},
                "tests": {
                    "com/acme/core/CoreTest.java": lib_test("com.acme.core", "Core"),
                    "com/acme/core/AbstractContract.java": ABSTRACT_FAILING,
                    "com/acme/core/CoreChecks.java": "package com.acme.core;\n\nclass CoreChecks extends AbstractContract {}\n",
                },
            },
            "web": {
                "deps": ["core"],
                "main": {"com/acme/web/Web.java": lib_main("com.acme.web", "Web")},
                "tests": {
                    "com/acme/web/WebTest.java": lib_test("com.acme.web", "Web"),
                    "com/acme/core/AbstractContract.java": "package com.acme.core;\n\nabstract class AbstractContract {}\n",
                },
            },
        }
    )


PROJECTS_SOURCES.update(
    {
        # a benchmark class: JMH's @Setup and @TearDown are on void methods, the class is never run by surefire
        "jmh": with_dependency(
            single(("CartTest", test_class("CartTest", ADDS)), ("CartBenchmark", JMH_BENCHMARK)),
            "org.openjdk.jmh",
            "jmh-core",
            "1.37",
        ),
        # CartChecks gets its failing test from Contract through CartContract, an interface that extends the one holding it
        "iface_chain": single(
            ("CartTest", test_class("CartTest", ADDS)),
            ("Contract", CONTRACT_INTERFACE),
            ("CartContract", test_class("CartContract", extends="").replace("class CartContract", "interface CartContract extends Contract")),
            ("CartChecks", test_class("CartChecks", extends=" implements CartContract")),
        ),
        "dupbase": dupbase(),
        # hand-written classes in packages named build and target: one undecidable, one with a test of its own that never runs
        "build_package": single(
            ("CartTest", test_class("CartTest", ADDS)),
            extra={
                "src/test/java/com/acme/build/CartFailure.java": test_class(
                    "CartFailure", 'CartFailure() {\n    super("x");\n}\n', pkg="com.acme.build", extends=" extends org.opentest4j.AssertionFailedError"
                ),
                "src/test/java/com/acme/target/CartChecks.java": test_class("CartChecks", CONTRACT, pkg="com.acme.target"),
            },
        ),
        # a JUnit 4 suite: the build has no vintage engine, so nothing runs it, and its name misses the patterns
        "runwith": with_dependency(
            single(("CartTest", test_class("CartTest", ADDS)), ("CartSuite", RUN_WITH_SUITE)), "junit", "junit", "4.13.2"
        ),
    }
)


def mvn_run(name, project, argv, request=None, before=None, timeout=300, cwd="."):
    return {"name": name, "project": project, "argv": argv, "request": request, "before": before, "timeout": timeout, "cwd": cwd}


T = "mvn -B --fail-at-end test".split()
SHOP_CART = "src/test/java/com/acme/shop/CartTest.java"
OFF = "src/test/java/com/acme/shop/OffTest.java"


def select(*selectors, extra=()):
    return ["mvn", "-B", "--fail-at-end", *extra, "test", "-Dtest=" + ",".join(selectors)]


def select_multi(modules, *selectors):
    return ["mvn", "-B", "--fail-at-end", "-pl", ",".join(modules), "-am", "test", "-Dtest=" + ",".join(selectors), "-Dsurefire.failIfNoSpecifiedTests=false"]


RUNS = [
    mvn_run("pass", "shop", T),
    mvn_run("fail", "shop_fail", T),
    mvn_run("main_compile_error", "main_compile_error", T),
    mvn_run("test_compile_error", "test_compile_error", T),
    mvn_run("exit_zero", "exit_zero", T),
    mvn_run("halt", "halt", T),
    mvn_run("junit_timeout", "junit_timeout", T),
    mvn_run("fork_timeout", "fork_timeout", T),
    mvn_run("ripple_timeout", "slow", T, timeout=40),
    mvn_run("disabled_all", "disabled", T),
    mvn_run("disabled_method", "disabled", select("com.acme.shop.CartTest#skipped"), [SHOP_CART + "::CartTest.skipped()"]),
    mvn_run("overloads_method", "overloads", select("com.acme.shop.CartTest#check"), ["com.acme.shop.CartTest.check()"]),
    mvn_run("not_picked_up", "not_picked_up", T),
    mvn_run("failure_ignored", "failure_ignored", T),
    mvn_run("skip_tests", "skip_tests", T),
    mvn_run("no_tests", "no_tests", T),
    mvn_run("method", "shop", select("com.acme.shop.CartTest#addsItems"), ["com.acme.shop.CartTest.addsItems()"]),
    mvn_run("method_params", "shop", select("com.acme.shop.CartTest#discounts"), ["com.acme.shop.CartTest.discounts(int)"]),
    mvn_run("method_nested", "shop", select("com.acme.shop.CartTest$Receipts#receiptHasTotal"), ["com.acme.shop.CartTest.Receipts.receiptHasTotal()"]),
    mvn_run("methods_two", "shop", select("com.acme.shop.CartTest#addsItems+discounts"), ["com.acme.shop.CartTest.addsItems()", "com.acme.shop.CartTest.discounts(int)"]),
    mvn_run("class_file", "shop", select("com.acme.shop.CartTest"), [SHOP_CART]),
    mvn_run("method_nomatch", "shop", select("com.acme.shop.CartTest#nosuch"), None),
    mvn_run("class_nomatch", "shop", select("com.acme.shop.CartTes"), None),
    mvn_run("method_fail", "shop_fail", select("com.acme.shop.CartTest#totalIsWrong"), ["com.acme.shop.CartTest.totalIsWrong()"]),
    mvn_run("dir", "shop", select("com.acme.shop.CartTest", "com.acme.shop.CartTests"), ["src/test/java/com/acme/shop"]),
    mvn_run("stale_method_nomatch", "shop", select("com.acme.shop.CartTest#nosuch"), None, before="pass"),
    mvn_run("stale_test_compile_error", "shop", ["mvn", "-B", "--fail-at-end", "test"], None, before="pass"),
    mvn_run("multimod_fail", "multimod_fail", T),
    mvn_run("multimod_class", "multimod_ok", select_multi(["web"], "com.acme.web.WebTest"), ["web/src/test/java/com/acme/web/WebTest.java"]),
    mvn_run("samename_b", "samename", select_multi(["b"], "com.acme.SameTest"), ["b/src/test/java/com/acme/SameTest.java"]),
    mvn_run("samename_all", "samename", T),
    # requirements: a thing asked for keeps its own obligation whatever broader request covers it
    mvn_run("disabled_dir_plus_file", "disabled", T, [".", OFF]),
    mvn_run("disabled_file_plus_method", "disabled", select("com.acme.shop.CartTest"), [SHOP_CART, SHOP_CART + "::CartTest.skipped()"]),
    mvn_run("class_plus_nested", "shop", select("com.acme.shop.CartTest"), [SHOP_CART, SHOP_CART + "::CartTest.Receipts"]),
    mvn_run("method_twice", "shop", select("com.acme.shop.CartTest#addsItems"), ["com.acme.shop.CartTest.addsItems()", SHOP_CART + "::CartTest.addsItems"]),
    mvn_run("module_dir", "multimod_ok", ["mvn", "-B", "--fail-at-end", "-pl", "core", "-am", "test"], ["core"]),
    # predictions: what ripple cannot read off the sources, it asks the compiled classes and Maven's own reactor
    mvn_run("not_picked_up_inherited", "inherited", T),
    mvn_run("not_picked_up_meta", "meta_annotated", T),
    mvn_run("undecidable_class", "undecidable", T),
    mvn_run("helper_class", "helper_class", T),
    mvn_run("testdir_parent", "testdir_parent", T),
    mvn_run("testdir_helper", "testdir_helper", T),
    mvn_run("am_profile", "am_profile", select_multi(["web"], "com.acme.web.WebTest"), ["web/src/test/java/com/acme/web/WebTest.java"]),
    mvn_run(
        "method_factory",
        "factory",
        select("com.acme.shop.CartTest#dynamicTests+repeated"),
        ["com.acme.shop.CartTest.dynamicTests()", "com.acme.shop.CartTest.repeated()"],
    ),
    # review round 2
    mvn_run("jmh_benchmark", "jmh", T),
    mvn_run("iface_chain", "iface_chain", T),
    mvn_run("dupbase", "dupbase", T),
    # the whole build ran first: web's classes are left in web/target, stale for a run of core alone
    mvn_run("dupbase_core", "dupbase", ["mvn", "-B", "--fail-at-end", "-pl", "core", "-am", "test"], ["core"], before="dupbase"),
    mvn_run("build_package", "build_package", T),
    mvn_run("runwith_suite", "runwith", T),
]
# a run whose earlier run is `before` first breaks the project the way `edit` says; the stale_* runs reuse "shop"
EDITS = {
    "stale_test_compile_error": {"src/test/java/com/acme/shop/CartTest.java": test_class("CartTest", ADDS, "@Test\nvoid broken() {\n    new Cart().nosuch();\n}\n", PARAMS, NESTED)},
}


def write_projects() -> None:
    shutil.rmtree(PROJECTS, ignore_errors=True)
    for name, files in PROJECTS_SOURCES.items():
        for rel, text in files.items():
            path = PROJECTS / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def env() -> dict:
    out = dict(os.environ)
    out["MAVEN_OPTS"] = ""
    out.pop("MAVEN_ARGS", None)
    return out


def prepare(project: str, root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(PROJECTS / project, root)
    config = root / ".mvn"
    config.mkdir()
    (config / "maven.config").write_text(f"-Dmaven.repo.local={M2}\n-o\n")


def run_tool(mvn: str, argv: list[str], cwd: Path, timeout: float) -> tuple[str, int, bool]:
    with open(cwd / ".out", "w+b") as out:
        proc = subprocess.Popen([mvn] + argv[1:], cwd=cwd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env())
        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            timed_out = True
        out.seek(0)
        text = out.read().decode(errors="replace")
    (cwd / ".out").unlink()
    return text, proc.returncode, timed_out


def reports_of(root: Path) -> dict[str, Path]:
    found = {}
    for path in sorted(root.rglob("surefire-reports/TEST-*.xml")):
        module = path.parent.parent.parent.relative_to(root).as_posix()
        found[f"{module}/{path.name}"] = path
    return found


def keep(files: dict[str, Path], destination: Path) -> None:
    for rel, path in files.items():
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def keep_classes(root: Path, destination: Path) -> None:
    """The compiled classes Maven left (target/classes and target/test-classes of every module), as <module>/<kind>/<path>.class."""
    for kind in ("classes", "test-classes"):
        for directory in sorted(root.rglob(f"target/{kind}")):
            module = directory.parent.parent.relative_to(root).as_posix()
            module = "_" if module == "." else module  # the root module: no directory of its own
            for path in sorted(directory.rglob("*.class")):
                target = destination / module / kind / path.relative_to(directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)


def capture(mvn: str, only: list[str]) -> None:
    """Capture every run, or just the named ones (their directories are replaced, the others stay)."""
    if not only:
        shutil.rmtree(CAPTURES, ignore_errors=True)
    CAPTURES.mkdir(exist_ok=True)
    for spec in RUNS:
        if only and spec["name"] not in only:
            continue
        root = WORK / spec["name"]
        prepare(spec["project"], root)
        out = CAPTURES / spec["name"]
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        if spec["before"]:
            earlier = next(r for r in RUNS if r["name"] == spec["before"])
            run_tool(mvn, earlier["argv"], root, 300)
            keep(reports_of(root), out / "before")
            for rel, text in EDITS.get(spec["name"], {}).items():
                (root / rel).write_text(text)
        started = time.time()
        stamps = {rel: path.stat().st_mtime_ns for rel, path in reports_of(root).items()}
        text, code, timed_out = run_tool(mvn, spec["argv"], root / spec["cwd"], spec["timeout"])
        (out / "stdout.txt").write_text(text.replace(str(root), "<repo>"))
        keep(reports_of(root), out / "reports")
        keep_classes(root, out / "classes")
        version = subprocess.run([mvn, "-v"], capture_output=True, text=True, env=env()).stdout.splitlines()[0]
        meta = {
            "project": spec["project"],
            "cwd": spec["cwd"],
            "argv": spec["argv"],
            "request": spec["request"],
            "before": spec["before"],
            # the report files this run wrote (new, or their mtime changed): the others are left over from `before`
            "written": sorted(rel for rel, path in reports_of(root).items() if stamps.get(rel) != path.stat().st_mtime_ns),
            "edits": EDITS.get(spec["name"]),
            "exit_code": None if timed_out else code,
            "timed_out": timed_out,
            "timeout_s": spec["timeout"],
            "maven": version,
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(spec["name"], meta["exit_code"], "timed out" if timed_out else "", f"{time.time() - started:.1f}s")
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    write_projects()
    capture(sys.argv[1] if len(sys.argv) > 1 else "mvn", sys.argv[2:])
