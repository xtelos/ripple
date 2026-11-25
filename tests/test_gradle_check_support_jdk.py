"""Which JDK the live Gradle 8 tests run on: the lookup in tests/gradle_check_support.py, tested with stand-in JDK directories.

Gradle 8.10.2 runs on Java 8 to 23 and dies on a newer one ("Unsupported class file major version 69" on JDK 25, which is what
the CI runner image's default JDK is), while Gradle 9.9.1 runs on it. So the Gradle 8 tests take their JDK from
RIPPLE_TEST_GRADLE8_JAVA_HOME, else from the JDK every other live test uses, and a JDK that Gradle 8 cannot run on is a reason,
named, for the live module to skip (and, with RIPPLE_REQUIRE_LIVE_GRADLE=1, to fail). No Gradle or real JDK is needed here.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from gradle_check_support import gradle8_java, java_major

VAR = "RIPPLE_TEST_GRADLE8_JAVA_HOME"


def fake_jdk(root: Path, banner: str, name: str = "jdk") -> str:
    """A directory with bin/javac and a bin/java that prints `banner` on stderr, as `java -version` does."""
    bin_dir = root / name / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "javac").write_text("#!/bin/sh\n")
    java = bin_dir / "java"
    java.write_text(f"#!/bin/sh\necho '{banner}' >&2\n")
    for tool in (bin_dir / "javac", java):
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return str(root / name)


JDK25 = 'openjdk version "25.0.5" 2025-10-21 LTS'
JDK21 = 'openjdk version "21.0.13" 2026-01-20 LTS'
JDK17 = 'openjdk version "17.0.21" 2026-04-21'
JDK8 = 'openjdk version "1.8.0_512"'


@pytest.mark.parametrize(("banner", "major"), [(JDK25, 25), (JDK21, 21), (JDK17, 17), (JDK8, 8)])
def test_the_major_version_is_read_from_java_dash_version(tmp_path, banner, major):
    assert java_major(fake_jdk(tmp_path, banner)) == major


def test_an_unreadable_java_has_no_major_version(tmp_path):
    assert java_major(str(tmp_path / "nowhere")) is None
    assert java_major(fake_jdk(tmp_path, "no version here")) is None


def test_the_variable_names_the_jdk_whatever_java_home_is(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVA_HOME", fake_jdk(tmp_path, JDK25, "default"))
    monkeypatch.setenv(VAR, fake_jdk(tmp_path, JDK21, "pinned"))
    assert gradle8_java() == (str(tmp_path / "pinned"), None)


def test_without_the_variable_the_jdk_of_the_other_live_tests_is_used(tmp_path, monkeypatch):
    home = fake_jdk(tmp_path, JDK17)
    monkeypatch.setenv("JAVA_HOME", home)
    monkeypatch.delenv(VAR, raising=False)
    assert gradle8_java() == (home, None)


def test_a_default_jdk_newer_than_gradle_8_supports_is_a_named_reason_not_a_run(tmp_path, monkeypatch):
    home = fake_jdk(tmp_path, JDK25)
    monkeypatch.setenv("JAVA_HOME", home)
    monkeypatch.delenv(VAR, raising=False)
    found, why = gradle8_java()
    assert found == home
    assert why is not None and "JDK 25" in why and "Gradle 8.10.2" in why and VAR in why, why


def test_a_pinned_jdk_that_is_too_new_is_the_same_reason(tmp_path, monkeypatch):
    monkeypatch.setenv(VAR, fake_jdk(tmp_path, JDK25))
    _, why = gradle8_java()
    assert why is not None and "JDK 25" in why and "8 to 23" in why, why


def test_the_oldest_and_newest_supported_jdks_are_accepted(tmp_path, monkeypatch):
    for banner in (JDK8, 'openjdk version "23.0.2" 2025-01-21'):
        monkeypatch.setenv(VAR, fake_jdk(tmp_path, banner, "j" + str(len(banner))))
        assert gradle8_java()[1] is None, banner


def test_a_variable_that_is_not_a_jdk_is_a_reason_and_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVA_HOME", fake_jdk(tmp_path, JDK21, "default"))
    monkeypatch.setenv(VAR, str(tmp_path / "no-such-dir"))
    found, why = gradle8_java()
    assert found == str(tmp_path / "no-such-dir")
    assert why is not None and VAR in why and "not a JDK" in why, why


def test_a_jdk_whose_version_cannot_be_read_is_a_reason(tmp_path, monkeypatch):
    monkeypatch.setenv(VAR, fake_jdk(tmp_path, "gibberish"))
    _, why = gradle8_java()
    assert why is not None and "version" in why, why
