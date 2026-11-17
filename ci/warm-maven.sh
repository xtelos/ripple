#!/bin/sh
# Fill a Maven repository with everything tests/test_check_java_live.py needs, so that it can then run `mvn -o`.
#
# usage: ci/warm-maven.sh <maven-repo-dir>
#
# It builds three of the repo's own fixtures once, online, into <maven-repo-dir>: jmh (JUnit 5.11.3, JMH 1.37,
# surefire 3.5.2, and so the shape every other live fixture has), runwith (adds JUnit 4.13.2) and testdir_helper
# (adds build-helper-maven-plugin 3.6.2). Running a test phase also makes Maven fetch the compiler and resources
# plugins and the surefire JUnit provider, so those versions come from the Maven that is installed, not from a list
# kept here. Run it again over a filled directory: Maven then fetches nothing.
# Artifacts come from Maven Central, through Maven, and from nowhere else.
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <maven-repo-dir>" >&2
    exit 2
fi
mkdir -p "$1"
repo=$(cd "$1" && pwd)
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for name in jmh runwith testdir_helper; do
    cp -R "$root/tests/fixtures/java_check/projects/$name" "$work/$name"
    # test failures are fine here (only the downloads matter); a build that cannot resolve is not
    mvn -B -ntp -Dmaven.repo.local="$repo" -Dmaven.test.failure.ignore=true -f "$work/$name/pom.xml" test
done
test -d "$repo/org/junit/jupiter/junit-jupiter/5.11.3"
test -d "$repo/org/openjdk/jmh/jmh-core/1.37"
test -d "$repo/junit/junit/4.13.2"
test -d "$repo/org/codehaus/mojo/build-helper-maven-plugin/3.6.2"
