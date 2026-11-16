"""Where a compiled class is looked up: as its own module's build would, never across the whole repo by name.

The classes are the ones a real javac run left (tests/fixtures/java_check/captures/dupbase): core and web each have a
`com.acme.core.AbstractContract`, core's holds a failing test and web's none. A class file with no source of its own in
the module's source roots is left over or generated, and is evidence of nothing in this run.
"""

from __future__ import annotations

import pytest
from java_check_support import CAPTURES, make_repo, place_classes

from ripple import java_check as jc

ABSTRACT = "com/acme/core/AbstractContract"
CORE_SRC = "core/src/test/java/com/acme/core"


def index_of(tmp_path, capture="dupbase"):
    repo = make_repo(tmp_path, capture)
    place_classes(repo, CAPTURES / capture / "classes")
    return repo


def build(repo):
    project = jc.find_project(repo)
    return project, jc.ClassIndex(repo, project, jc.compiled_classes(repo))


def module(project, directory):
    return next(m for m in project.modules if m.dir == directory)


def holds_tests(info):
    return [m.name for m in info.methods if "org/junit/jupiter/api/Test" in m.annotations]


def test_a_module_finds_its_own_class_before_any_other_modules(tmp_path):
    project, index = build(index_of(tmp_path))
    assert holds_tests(index.scope(module(project, "core")).resolve(ABSTRACT)) == ["wouldFail"]
    assert holds_tests(index.scope(module(project, "web")).resolve(ABSTRACT)) == []


def test_a_module_sees_the_main_classes_of_the_modules_it_depends_on_and_not_of_the_others(tmp_path):
    project, index = build(index_of(tmp_path))
    assert index.scope(module(project, "web")).resolve("com/acme/core/Core").name == "com/acme/core/Core"
    assert index.scope(module(project, "core")).resolve("com/acme/web/Web") is None


def test_the_test_classes_of_a_dependency_are_not_on_the_classpath_without_a_test_jar(tmp_path):
    project, index = build(index_of(tmp_path))
    assert index.scope(module(project, "web")).resolve("com/acme/core/CoreChecks") is None


def app_module(tmp_path, dependencies):
    """dupbase plus `app`, which holds a copy of core's CoreChecks and depends on core and web as `dependencies` says."""
    repo = index_of(tmp_path)
    root = repo / "pom.xml"
    root.write_text(root.read_text().replace("</modules>", "<module>app</module></modules>"))
    (repo / "app").mkdir()
    (repo / "app" / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<parent><groupId>com.acme</groupId><artifactId>root</artifactId><version>1.0</version></parent>"
        f"<artifactId>app</artifactId><dependencies>{dependencies}</dependencies></project>"
    )
    source = repo / "app" / "src" / "test" / "java" / "com" / "acme" / "core"
    source.mkdir(parents=True)
    (source / "CoreChecks.java").write_text("package com.acme.core;\nclass CoreChecks extends AbstractContract {}\n")
    classes = repo / "app" / "target" / "test-classes" / "com" / "acme" / "core"
    classes.mkdir(parents=True)
    (classes / "CoreChecks.class").write_bytes((repo / "core" / "target" / "test-classes" / "com" / "acme" / "core" / "CoreChecks.class").read_bytes())
    return repo


def dependency(artifact, test_jar=False):
    kind = "<type>test-jar</type>" if test_jar else ""
    return f"<dependency><groupId>com.acme</groupId><artifactId>{artifact}</artifactId><version>1.0</version>{kind}</dependency>"


def test_a_test_jar_dependency_puts_that_modules_test_classes_on_the_classpath(tmp_path):
    project, index = build(app_module(tmp_path, dependency("core", test_jar=True)))
    assert holds_tests(index.scope(module(project, "app")).resolve(ABSTRACT)) == ["wouldFail"]
    plain = build(app_module(tmp_path / "plain", dependency("core") + dependency("web")))
    assert plain[1].scope(module(plain[0], "app")).resolve(ABSTRACT) is None


def test_a_name_found_in_two_modules_ripple_cannot_rank_is_ambiguous_and_the_class_is_undecided(tmp_path):
    project, index = build(app_module(tmp_path, dependency("core", test_jar=True) + dependency("web", test_jar=True)))
    scope = index.scope(module(project, "app"))
    assert scope.resolve(ABSTRACT) is jc.AMBIGUOUS
    own = next(c.info for c in jc.compiled_classes(tmp_path / "repo") if c.relative.startswith("app/"))
    result = jc.decide(own, scope, custom_patterns=False)
    assert result.kind == "unknown" and "more than one" in result.why and ABSTRACT in result.why


def test_a_dependency_is_followed_through_the_modules_it_depends_on(tmp_path):
    """web depends on core: app depending on web alone still sees core's main classes."""
    project, index = build(app_module(tmp_path, dependency("web")))
    assert index.scope(module(project, "app")).resolve("com/acme/core/Core").name == "com/acme/core/Core"


def test_a_dependency_cycle_in_the_poms_ends(tmp_path):
    repo = app_module(tmp_path, dependency("web"))
    core = repo / "core" / "pom.xml"
    core.write_text(core.read_text().replace("</project>", f"<dependencies>{dependency('app')}</dependencies></project>"))
    project, index = build(repo)
    assert index.scope(module(project, "app")).resolve("com/acme/core/Core") is not None
    assert index.scope(module(project, "core")).resolve("com/acme/nothing/There") is None


def test_a_dependency_written_in_a_profile_is_still_read(tmp_path):
    repo = app_module(tmp_path, "")
    pom = repo / "app" / "pom.xml"
    pom.write_text(pom.read_text().replace("</project>", f"<profiles><profile><id>p</id><dependencies>{dependency('web')}</dependencies></profile></profiles></project>"))
    project, index = build(repo)
    assert index.scope(module(project, "app")).resolve("com/acme/core/Core") is not None


def test_a_class_with_no_source_in_the_modules_source_roots_is_not_found(tmp_path):
    repo = index_of(tmp_path)
    (repo / CORE_SRC / "AbstractContract.java").unlink()  # the class file is left over from an earlier build
    project, index = build(repo)
    assert index.scope(module(project, "core")).resolve(ABSTRACT) is None


def test_a_class_is_matched_to_its_source_by_package_path_and_outer_class_name(tmp_path):
    """CoreChecks$Inner and CoreChecks$1 follow CoreChecks; a source of the same name in another package is no source."""
    repo = index_of(tmp_path, "pass")
    project, index = build(repo)
    only = project.modules[0]
    sourced = {c.info.name for c in index.items(only, "test-classes") if index.sourced(only, c)}
    assert sourced == {"com/acme/shop/CartTest", "com/acme/shop/CartTest$Receipts", "com/acme/shop/CartTests"}
    anonymous = jc.Compiled("target/test-classes/x/CartTest$1.class", "test-classes", jc.ClassInfo("com/acme/shop/CartTest$1", "java/lang/Object", False, False, frozenset(), (), ()))
    assert index.sourced(only, anonymous) is True
    source = repo / "src" / "test" / "java" / "com" / "acme" / "shop"
    (source.parent / "other").mkdir()
    (source / "CartTest.java").rename(source.parent / "other" / "CartTest.java")
    project, index = build(repo)
    only = project.modules[0]
    sourced = {c.info.name for c in index.items(only, "test-classes") if index.sourced(only, c)}
    assert sourced == {"com/acme/shop/CartTests"}
    assert index.sourced(only, anonymous) is False


def test_where_the_source_roots_cannot_be_read_every_class_counts(tmp_path):
    """A root held in a property: ripple cannot tell a stale class from a current one, so it does not call any stale."""
    repo = index_of(tmp_path, "testdir_helper")
    project, index = build(repo)
    only = project.modules[0]
    assert only.roots_uncertain
    assert all(index.sourced(only, c) for c in index.items(only, "test-classes"))


def test_a_class_a_kotlin_or_groovy_compiler_wrote_is_not_judged_by_java_sources(tmp_path):
    repo = index_of(tmp_path)
    project, index = build(repo)
    core = module(project, "core")
    item = next(c for c in index.items(core, "test-classes") if c.relative.endswith("CoreChecks.class"))
    kotlin = jc.Compiled(item.relative, item.kind, jc.ClassInfo("com/acme/core/Made", "java/lang/Object", False, False, frozenset(), (), (), "Made.kt"))
    assert index.sourced(core, kotlin) is True


@pytest.mark.parametrize("name", ["build", "target"])
def test_a_source_in_a_package_named_build_or_target_is_a_source(tmp_path, name):
    repo = index_of(tmp_path)
    source = repo / "core" / "src" / "test" / "java" / "com" / "acme" / name
    source.mkdir()
    (source / "Made.java").write_text(f"package com.acme.{name};\nclass Made {{}}\n")
    project, index = build(repo)
    core = module(project, "core")
    made = jc.Compiled("core/target/test-classes/x/Made.class", "test-classes", jc.ClassInfo(f"com/acme/{name}/Made", "java/lang/Object", False, False, frozenset(), (), (), "Made.java"))
    assert index.sourced(core, made) is True
