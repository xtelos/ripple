#!/bin/sh
# Install everything the three live Gradle test modules look for, so that they can then run `gradle --offline`:
# tests/test_check_gradle_live.py (Gradle 9.9.1), tests/test_check_gradle_live8.py (Gradle 8.10.2) and
# tests/test_check_gradle_live_junit4.py (JUnit 4.13.2 on both).
#
# usage: ci/warm-gradle.sh <install-dir> [<maven-repo-dir>]
#
# <install-dir> gets:
#   gradle-9.9.1/ and gradle-8.10.2/   the distributions, each downloaded from services.gradle.org and checked against the
#                                      SHA-256 pinned below (the one Gradle publishes beside the zip) BEFORE it is unpacked;
#                                      a mismatch stops the script with nothing unpacked
#   dl/gradle-9.9.1-bin.zip            kept: test_the_repos_own_wrapper_is_preferred_and_runs points a wrapper at it
#   home9/ and home8/                  a Gradle user home per Gradle, filled online by building ci/warm-gradle once with it:
#                                      JUnit 5.11.3 through the junit-bom, the platform launcher, and an integrationTest
#                                      JvmTestSuite (which resolves JUnit Jupiter itself). Running the tests, not only resolving,
#                                      makes Gradle fetch what the test task needs at run time. Everything comes from Maven
#                                      Central, through Gradle.
# <maven-repo-dir> (default <install-dir>/m2) gets junit 4.13.2, hamcrest-core 1.3 and hamcrest-parent 1.3 (jar and pom each),
# each downloaded from Maven Central and checked against a pinned SHA-256 before it is moved into place. A file already there
# with the pinned content is kept (ci/warm-maven.sh leaves the same files), one there with other content stops the script.
#
# Gradle 9.9.1 runs on the JDK in JAVA_HOME (else on PATH). Gradle 8.10.2 dies on a JDK newer than 23 ("Unsupported class file
# major version 69" on JDK 25), so its build runs on RIPPLE_TEST_GRADLE8_JAVA_HOME when that is set (else on the same JDK), and
# the script refuses a Gradle 8 JDK newer than 23 before it starts. It prints `java -version` before each Gradle build.
#
# Versions are exact on purpose: they are the ones the tests were measured on. Run it again over a filled directory: nothing
# is downloaded and Gradle fetches nothing. The last lines print the environment the tests read.
set -eu

GRADLE_URL=https://services.gradle.org/distributions
MAVEN_URL=https://repo.maven.apache.org/maven2
GRADLE_9=9.9.1
GRADLE_9_SHA256=d8a18a7a841dd5df825d35a685fad6f2d7f12ac0c280e57b67c27b4a520ba228
GRADLE_8=8.10.2
GRADLE_8_SHA256=31c55713e40233a8303827ceb42ca48a47267a0ad4bab9177123121e71524c26

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "usage: $0 <install-dir> [<maven-repo-dir>]" >&2
    exit 2
fi
mkdir -p "$1"
dir=$(cd "$1" && pwd)
mkdir -p "${2:-$dir/m2}"
m2=$(cd "${2:-$dir/m2}" && pwd)
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$dir/dl"

sha_of() { sha256sum "$1" | cut -d ' ' -f 1; }
fetch() { curl --fail --silent --show-error --location --retry 3 --proto '=https' --tlsv1.2 --output "$2" "$1"; }

# a Gradle distribution: a zip that is here and verified, else downloaded and verified, and only then unpacked
install_gradle() { # version sha256 keep-zip
    version=$1 want=$2 keep=$3
    zip="$dir/dl/gradle-$version-bin.zip"
    installed=no
    if [ -x "$dir/gradle-$version/bin/gradle" ]; then installed=yes; fi
    if [ "$installed" = no ] || [ "$keep" = keep ]; then
        if [ ! -f "$zip" ] || [ "$(sha_of "$zip")" != "$want" ]; then
            rm -f "$zip"
            echo "gradle $version: downloading $GRADLE_URL/gradle-$version-bin.zip"
            fetch "$GRADLE_URL/gradle-$version-bin.zip" "$work/gradle-$version-bin.zip"
            got=$(sha_of "$work/gradle-$version-bin.zip")
            if [ "$got" != "$want" ]; then
                echo "gradle $version: SHA-256 mismatch, nothing unpacked (wanted $want, got $got)" >&2
                exit 1
            fi
            mv "$work/gradle-$version-bin.zip" "$zip"
        fi
    fi
    if [ "$installed" = yes ]; then
        echo "gradle $version: already installed"
    else
        echo "gradle $version: SHA-256 verified, unpacking"
        rm -rf "$work/unpack-$version"
        mkdir "$work/unpack-$version"
        # python, not unzip: it is on every runner that ran setup-python, and it is the one extractor tried here
        python3 -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$zip" "$work/unpack-$version"
        chmod +x "$work/unpack-$version/gradle-$version/bin/gradle"
        rm -rf "$dir/gradle-$version"
        mv "$work/unpack-$version/gradle-$version" "$dir/gradle-$version"
    fi
    if [ "$keep" != keep ]; then rm -f "$zip"; fi
}

# a file of a Maven repository: keep one that is already right, refuse one that is not, else download, verify, move in
install_artifact() { # path-under-the-repository sha256
    rel=$1 want=$2
    dest="$m2/$rel"
    if [ -f "$dest" ]; then
        if [ "$(sha_of "$dest")" != "$want" ]; then
            echo "$dest exists but is not the pinned file (wanted SHA-256 $want)" >&2
            exit 1
        fi
        return 0
    fi
    echo "maven: downloading $rel"
    fetch "$MAVEN_URL/$rel" "$work/artifact"
    got=$(sha_of "$work/artifact")
    if [ "$got" != "$want" ]; then
        echo "$rel: SHA-256 mismatch, not installed (wanted $want, got $got)" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$dest")"
    mv "$work/artifact" "$dest"
}

# the user home of one Gradle, filled by one online build of the warm project, on the JDK given (else the one in JAVA_HOME or on PATH)
warm_home() { # version home java-home-or-empty
    (
        if [ -n "$3" ]; then
            JAVA_HOME=$3
            PATH="$3/bin:$PATH"
            export JAVA_HOME PATH
        fi
        java=java
        if [ -n "${JAVA_HOME:-}" ]; then java="$JAVA_HOME/bin/java"; fi
        echo "gradle $1: filling $2 (online) on this JDK (JAVA_HOME=${JAVA_HOME:-unset}):"
        "$java" -version 2>&1 | sed 's/^/    /'
        if [ "$1" = "$GRADLE_8" ]; then
            major=$("$java" -version 2>&1 | sed -n '1s/.*version "\(1\.\)\{0,1\}\([0-9]*\).*/\2/p')
            if [ -z "$major" ] || [ "$major" -gt 23 ]; then
                echo "gradle $1 runs on Java 8 to 23 and this JDK is ${major:-unreadable} (set RIPPLE_TEST_GRADLE8_JAVA_HOME to a JDK of 17 to 23)" >&2
                exit 1
            fi
        fi
        rm -rf "$work/warm"
        cp -R "$root/ci/warm-gradle" "$work/warm"
        GRADLE_USER_HOME="$2" "$dir/gradle-$1/bin/gradle" --version
        GRADLE_USER_HOME="$2" "$dir/gradle-$1/bin/gradle" --no-daemon --console=plain -p "$work/warm" test integrationTest
        test -d "$2/caches/modules-2/files-2.1/org.junit.jupiter/junit-jupiter/5.11.3"
        test -d "$2/caches/modules-2/files-2.1/org.junit.platform/junit-platform-launcher"
    )
}

install_gradle "$GRADLE_9" "$GRADLE_9_SHA256" keep
install_gradle "$GRADLE_8" "$GRADLE_8_SHA256" drop

install_artifact junit/junit/4.13.2/junit-4.13.2.jar 8e495b634469d64fb8acfa3495a065cbacc8a0fff55ce1e31007be4c16dc57d3
install_artifact junit/junit/4.13.2/junit-4.13.2.pom 569b6977ee4603c965c1c46c3058fa6e969291b0160eb6964dd092cd89eadd94
install_artifact org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar 66fdef91e9739348df7a096aa384a5685f4e875584cce89386a7a47251c4d8e9
install_artifact org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.pom fde386a7905173a1b103de6ab820727584b50d0e32282e2797787c20a64ffa93
install_artifact org/hamcrest/hamcrest-parent/1.3/hamcrest-parent-1.3.pom 6d535f94efb663bdb682c9f27a50335394688009642ba7a9677504bc1be4129b

warm_home "$GRADLE_9" "$dir/home9" ""
warm_home "$GRADLE_8" "$dir/home8" "${RIPPLE_TEST_GRADLE8_JAVA_HOME:-}"

test -f "$dir/dl/gradle-$GRADLE_9-bin.zip"
test -f "$m2/junit/junit/4.13.2/junit-4.13.2.jar"
test -d "$m2/org/hamcrest/hamcrest-core/1.3"
echo
echo "installed under $dir. The live Gradle tests read:"
echo "  PATH=$dir/gradle-$GRADLE_9/bin:\$PATH"
echo "  RIPPLE_TEST_GRADLE_HOME=$dir/home9"
echo "  RIPPLE_TEST_GRADLE8=$dir/gradle-$GRADLE_8/bin/gradle"
echo "  RIPPLE_TEST_GRADLE8_HOME=$dir/home8"
echo "  RIPPLE_TEST_GRADLE_DIST=$dir/dl/gradle-$GRADLE_9-bin.zip"
echo "  RIPPLE_TEST_M2=$m2"
echo "  JAVA_HOME=<a JDK 17 or later, for Gradle 9>"
echo "  RIPPLE_TEST_GRADLE8_JAVA_HOME=<a JDK of 17 to 23, for Gradle 8; unset means JAVA_HOME>"
