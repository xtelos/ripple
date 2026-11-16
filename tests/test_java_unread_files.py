"""The walk for .java files that ripple does not read (Declarations.note_unread_sources) opens only what is a regular file in the repo.

A name that ends in .java is not always a source file. At 9e7e509 the walk opened every one with read_bytes(): a named pipe called
`Gen.java` blocked it for ever (the main walk refuses them: indexer._may_be_file), a link to /dev/zero never ended, and a dangling or
looping link raised OSError, which was read as "a file that cannot be read may declare anything" and made every call of the repo
unresolved with no reason in the output. Each case below is a real file-system object under tmp_path, run under a deadline, so a
hang is a failure and not a stuck suite.

What each is, and what javac does with it (a named pipe, a device and a dangling link are no source file: javac reports `error: file
not found` or blocks, so none is compiled; a link to a file outside the repo is compiled when the build names it):
  regular file inside the repo     read (at most UNREAD_LIMIT bytes), and what it declares is noted
  named pipe, device, socket, dir  never opened; declares nothing
  link to nothing / around a loop  declares nothing (a file that is not there)
  link out of the repo             not read (it is not repo code ripple may look at), and it may declare anything: listed, calls unresolved
  larger than UNREAD_LIMIT         not scanned; may declare any method, and any type of the package its first bytes say
  bytes that are not UTF-8         read as ISO-8859-1, which maps every byte, so no name is hidden; UTF-16 and UTF-32 (a mark, or a
                                   quarter of the bytes NUL) are decoded
"""

from __future__ import annotations

import os
import signal
import stat
from contextlib import contextmanager

import pytest

pytest.importorskip("tree_sitter_java")

from ripple import java_indexer  # noqa: E402
from ripple.indexer import build_graph  # noqa: E402
from ripple.java_indexer import Declarations, load_parser, read_unread_source  # noqa: E402
from ripple.indexer import walk_repo  # noqa: E402
from ripple.profile import JAVA  # noqa: E402

MARKERS = {"pom.xml": "<project/>"}
BASE = {
    "p/Use.java": "package p;\nclass Use { void go(java.util.List<String> l) { l.size(); helper(); } void helper() {} }\n",
    "p/Use2.java": "package p;\nimport q.*;\nclass Use2 { void go(Thing t) { t.anything(); } }\n",
    "q/Thing.java": "package q;\npublic class Thing { public void anything() {} }\n",
}
EDGE = ("p.Use2.go(Thing)", "q.Thing.anything()")


@contextmanager
def deadline(seconds: int = 20):
    """Fail, not hang: SIGALRM interrupts a blocked open() or read() in the main thread."""
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("no SIGALRM")

    def expired(signum, frame):
        raise TimeoutError(f"still running after {seconds} s: the walk is blocked on something it should never have opened")

    before = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, before)


def index(make_repo, files: dict | None = None):
    root = make_repo({**MARKERS, **(BASE if files is None else files)})
    with deadline():
        return root, build_graph(root, JAVA)


def edges(g) -> set[tuple[str, str]]:
    return {(e.caller, e.callee) for e in g.edges}


def untouched(g) -> bool:
    """The repo says what it says with no special file in it: the edge through the on-demand import, `l.size()` external (no source
    declares a `size`), and nothing of Use2 unresolved."""
    return EDGE in edges(g) and not [u for u in g.unresolved if u.text == "l.size" or u.caller == "p.Use2.go(Thing)"] and g.external_calls >= 1


def size_is_unproven(g) -> bool:
    """`l.size()` was external with no unread file: a file that may declare a `size` makes it unresolved."""
    return bool([u for u in g.unresolved if u.text == "l.size"])


# ---- never open what is not a regular file --------------------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes")
@pytest.mark.parametrize("where", ["target/Gen.java", "src/main/java/p/Pipe.java", "p/Pipe.java", ".hidden/Gen.java"])
def test_a_named_pipe_called_x_java_is_never_opened(make_repo, tmp_path, where):
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / where).parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(tmp_path / where)
    assert stat.S_ISFIFO(os.lstat(tmp_path / where).st_mode)
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)
    assert [e for e in g.parse_errors if "Gen.java" in e or "Pipe.java" in e] == []  # (it is no source: nothing to list)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes")
def test_a_named_pipe_with_a_writer_waiting_is_not_read_either(make_repo, tmp_path):
    # a writer that would feed an endless stream of `class` words into the scan: the pipe is never opened, so it never gets the chance
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.mkfifo(tmp_path / "target" / "Feed.java")
    fd = os.open(tmp_path / "target" / "Feed.java", os.O_RDWR | os.O_NONBLOCK)  # keeps the pipe open both ways, so a reader would not see EOF
    try:
        os.write(fd, b"package q;\nclass Thing { void anything() {} }\n")
        with deadline():
            g = build_graph(root, JAVA)
    finally:
        os.close(fd)
    assert untouched(g)


def test_a_link_to_dev_zero_is_never_read(make_repo, tmp_path):
    if not os.path.exists("/dev/zero"):
        pytest.skip("no /dev/zero")
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.symlink("/dev/zero", tmp_path / "target" / "Zero.java")
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)  # (a device is no source file: it declares nothing, and a link out of the repo to one is not read at all)


def test_a_directory_called_x_java_is_walked_not_read(make_repo, tmp_path):
    root = make_repo({**MARKERS, **BASE, "target/Dir.java/Inner.java": "package q;\nclass Inner { int size() { return 0; } }\n"})
    with deadline():
        g = build_graph(root, JAVA)
    assert size_is_unproven(g)  # Inner.java is an unread file below target/ (the directory is walked): it declares a `size`


def test_a_socket_called_x_java_is_never_opened(make_repo, tmp_path):
    import socket

    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(tmp_path / "target" / "Sock.java"))
    except OSError:
        pytest.skip("cannot bind a socket here (path too long?)")
    try:
        with deadline():
            g = build_graph(root, JAVA)
    finally:
        server.close()
    assert untouched(g)


# ---- links: leading nowhere, around a loop, out of the repo, inside it --------------------------------------------------------


def test_a_dangling_link_called_x_java_declares_nothing_and_floods_nothing(make_repo, tmp_path, tmp_path_factory):
    # 9e7e509: the OSError set unseen_anywhere and unseen_unreadable, so `t.anything()` lost its edge and both externals went unresolved
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.symlink(tmp_path / "target" / "nowhere.java", tmp_path / "target" / "Gone.java")
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)
    plain = tmp_path_factory.mktemp("control")
    for rel, source in {**MARKERS, **BASE}.items():
        (plain / rel).parent.mkdir(parents=True, exist_ok=True)
        (plain / rel).write_text(source)
    c = build_graph(plain, JAVA)
    assert (edges(g), g.external_calls, len(g.unresolved)) == (edges(c), c.external_calls, len(c.unresolved))
    assert g.parse_errors == []


def test_a_looping_link_called_x_java_declares_nothing_and_floods_nothing(make_repo, tmp_path):
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.symlink(tmp_path / "target" / "B.java", tmp_path / "target" / "A.java")
    os.symlink(tmp_path / "target" / "A.java", tmp_path / "target" / "B.java")
    os.symlink("Self.java", tmp_path / "target" / "Self.java")
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)
    assert g.parse_errors == []


def test_a_link_to_a_file_outside_the_repo_is_not_read_and_may_declare_anything(make_repo, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "Thing.java").write_text("package q;\npublic class Thing { public void anything() {} }\n")
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.symlink(outside / "Thing.java", tmp_path / "target" / "Thing.java")
    opened: list[str] = []
    real_open = os.open

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    with deadline():
        os_open, os.open = os.open, spy
        try:
            g = build_graph(root, JAVA)
        finally:
            os.open = os_open
    assert not [p for p in opened if str(outside) in p], opened  # never opened
    assert EDGE not in edges(g) and size_is_unproven(g)  # it may declare anything: unresolved
    assert [e for e in g.parse_errors if "Thing.java" in e and "link out of the repo" in e]  # and the user is told which file and why


def test_a_link_to_a_file_inside_the_repo_is_read_as_that_file(make_repo, tmp_path):
    # a copy of a source under target/ through a link: the same bytes as a file that was read, so it declares nothing new
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    os.symlink(tmp_path / "q" / "Thing.java", tmp_path / "target" / "Thing.java")
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)


def test_a_link_to_an_unread_file_inside_the_repo_is_what_that_file_declares(make_repo, tmp_path):
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "node_modules" / "q").mkdir(parents=True)  # (a directory the walk does not look into: only the link shows the file)
    (tmp_path / "node_modules" / "q" / "Other.java").write_text("package q;\npublic class Other { public int size() { return 0; } }\n")
    (tmp_path / ".hidden").mkdir()
    os.symlink(tmp_path / "node_modules" / "q" / "Other.java", tmp_path / ".hidden" / "Linked.java")
    with deadline():
        g = build_graph(root, JAVA)
    assert size_is_unproven(g)  # `size` is declared in the file the link leads to (a link below a hidden directory)


# ---- the size cap -------------------------------------------------------------------------------------------------------------------


def test_a_file_larger_than_the_cap_is_not_scanned_and_may_declare_any_method_and_any_type_of_its_package(make_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(java_indexer, "UNREAD_LIMIT", 2048)
    monkeypatch.setattr(java_indexer, "UNREAD_HEAD", 512)
    padding = "// " + "x" * 80 + "\n"
    huge = "package q;\n" + padding * 60 + "public class Hidden { public void anything() {} }\n"
    assert len(huge) > 2048
    root = make_repo(
        {
            **MARKERS,
            **BASE,
            "target/gen/q/Hidden.java": huge,
            "p/Tail.java": "package p;\nimport q.Zed;\nimport r.Zed2;\nclass Tail { void go() { Zed.make(); Zed2.make(); } }\n",
        }
    )
    with deadline():
        g = build_graph(root, JAVA)
    # any type of ITS package may be declared in it (`q.Zed`), and no type of another (`r.Zed2` stays a library type)
    assert [u for u in g.unresolved if u.caller == "p.Tail.go()" and u.text == "Zed.make"]
    assert not [u for u in g.unresolved if u.caller == "p.Tail.go()" and u.text == "Zed2.make"]
    # it cannot be scanned, so it may declare a `size`: the absence of one is unproven. (The read `Thing.anything` keeps its edge: the
    # file is of package q, but declares what the read `Thing` does not.)
    assert size_is_unproven(g) and EDGE in edges(g)
    assert [e for e in g.parse_errors if "Hidden.java" in e and "not scanned" in e]
    d = declared_of(root)
    assert "q" in d.unseen_packages and d.unseen_unreadable and not d.unseen_anywhere  # its package, from its head: not every package


def declared_of(root) -> Declarations:
    declarations, parser = Declarations(), load_parser()
    paths, _ = walk_repo(root, JAVA)
    for path in paths:
        declarations.add_file(path, root, parser)
    declarations.note_unread_sources(root, paths)
    declarations.finish()
    return declarations


def test_a_file_larger_than_the_cap_with_no_package_in_its_head_is_in_the_default_package(make_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(java_indexer, "UNREAD_LIMIT", 1024)
    monkeypatch.setattr(java_indexer, "UNREAD_HEAD", 256)
    root = make_repo({**MARKERS, **BASE, "target/Big.java": "class Big {}\n" + "// padding padding padding padding padding\n" * 60})
    d = declared_of(root)
    assert "" in d.unseen_packages and d.unseen_unreadable and not d.unseen_anywhere


def test_a_file_larger_than_the_cap_whose_head_is_all_comment_may_be_in_any_package(make_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(java_indexer, "UNREAD_LIMIT", 1024)
    monkeypatch.setattr(java_indexer, "UNREAD_HEAD", 256)
    root = make_repo({**MARKERS, **BASE, "target/Big.java": "// licence licence licence licence licence licence\n" * 80})
    d = declared_of(root)
    assert d.unseen_anywhere and d.unseen_unreadable


def test_the_real_cap_is_four_mebibytes_and_a_file_below_it_is_scanned_whole(make_repo, tmp_path):
    assert java_indexer.UNREAD_LIMIT == 4 * 1024 * 1024
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    body = "package q;\npublic class Pad { public void scanned() {} }\n" + "// pad pad pad pad pad pad pad pad pad pad pad pad pad pad pad\n" * 60000
    (tmp_path / "target" / "Pad.java").write_text(body)
    assert 3 * 2**20 < len(body) < 4 * 2**20
    with deadline(60):
        d = declared_of(root)
    assert "scanned" in d.unseen_methods and not d.unseen_unreadable


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes")
def test_a_name_swapped_for_a_pipe_between_the_check_and_the_open_is_not_read(tmp_path, monkeypatch):
    # the check is made on the path and again on the descriptor: a path that looks like a regular file (os.lstat says so) and is a pipe
    # when it is opened is not read, and the open does not block
    pipe = tmp_path / "Swapped.java"
    os.mkfifo(pipe)
    real_lstat = os.lstat
    regular = real_lstat(__file__)

    def lstat(path, *args, **kwargs):
        return regular if str(path) == str(pipe) else real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(java_indexer.os, "lstat", lstat)
    with deadline(10):
        assert read_unread_source(str(pipe), os.path.realpath(tmp_path)) == ("absent", b"")


def test_a_file_one_byte_over_the_real_cap_is_read_no_further_than_the_cap(make_repo, tmp_path, monkeypatch):
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    big = tmp_path / "target" / "Big.java"
    with open(big, "wb") as f:
        f.write(b"package q;\nclass Big { void anything() {} }\n")
        f.seek(3 * java_indexer.UNREAD_LIMIT)
        f.write(b"x")  # (a sparse file three times the cap: nothing written between)
    taken = [0]
    real_read = os.read

    def counting(fd, n):
        data = real_read(fd, n)
        taken[0] += len(data)
        return data

    monkeypatch.setattr(java_indexer.os, "read", counting)
    kind, data = read_unread_source(str(big), os.path.realpath(root))
    assert kind == "huge" and len(data) == java_indexer.UNREAD_HEAD
    assert taken[0] == java_indexer.UNREAD_LIMIT + 1  # one byte more than the cap is all it asks for, to know it is over


# ---- the bytes of a file: encodings, and what is not text ------------------------------------------------------------------------


def encoded_site(make_repo, tmp_path, method: str, encoding: str):
    """A call `l.<method>()` on a java.util.List, where an unread file written in `encoding` declares the method (the receiver is a JDK
    type, so only the name of the method can make the call unresolved, and the type route is not what is tested)."""
    root = make_repo({**MARKERS, "p/Use.java": f"package p;\nclass Use {{ void go(java.util.List<String> l) {{ l.{method}(); }} }}\n"})
    (tmp_path / "target" / "gen" / "p").mkdir(parents=True)
    source = f"package p;\npublic class Group {{ public int {method}() {{ return 1; }} }}\n"
    (tmp_path / "target" / "gen" / "p" / "Group.java").write_bytes(source.encode(encoding))
    with deadline():
        return build_graph(root, JAVA)


def test_a_file_in_iso_8859_1_loses_no_declaration(make_repo, tmp_path):
    # javac -encoding ISO-8859-1: `größe` is the bytes 67 72 F6 DF 65, which are not UTF-8. They were decoded with errors="replace" and
    # the name split at U+FFFD, so the method `größe()` was missed and its call was external.
    g = encoded_site(make_repo, tmp_path, "größe", "latin-1")
    assert [u for u in g.unresolved if u.text == "l.größe"] and g.external_calls == 0


def test_a_file_in_utf_16_with_a_mark_is_decoded(make_repo, tmp_path):
    g = encoded_site(make_repo, tmp_path, "größe", "utf-16")
    assert [u for u in g.unresolved if u.text == "l.größe"] and g.external_calls == 0


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_a_file_in_utf_16_or_32_without_a_mark_is_decoded_too(make_repo, tmp_path, encoding):
    # NUL bytes between the letters would split every name into one-letter words
    g = encoded_site(make_repo, tmp_path, "größe", encoding)
    assert [u for u in g.unresolved if u.text == "l.größe"] and g.external_calls == 0


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_a_name_outside_latin_1_in_a_file_with_a_byte_order_mark_is_decoded(make_repo, tmp_path, encoding):
    # (dropping the NULs spells ASCII and Latin-1 names right; the mark is what spells a Greek one)
    g = encoded_site(make_repo, tmp_path, "μέθοδος", encoding)
    assert [u for u in g.unresolved if u.text == "l.μέθοδος"] and g.external_calls == 0


def test_a_file_in_utf_8_is_decoded_and_one_with_a_byte_order_mark_too(make_repo, tmp_path):
    g = encoded_site(make_repo, tmp_path, "größe", "utf-8-sig")
    assert [u for u in g.unresolved if u.text == "l.größe"] and g.external_calls == 0


def test_a_control_character_in_a_string_of_a_source_does_not_make_the_file_unreadable(make_repo, tmp_path):
    # commons-lang's ClassUtilsOssFuzzTest.java has raw control characters in a string literal: it is text, and declares what it declares
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Fuzz.java").write_bytes(b'package q;\nclass Fuzz { String s = "a\x01b\x00c"; }\n')
    with deadline():
        g = build_graph(root, JAVA)
    assert untouched(g)


def test_decode_source_tries_utf_8_then_iso_8859_1_and_nothing_hides_a_name():
    assert java_indexer.decode_source("größe".encode()) == "größe"
    assert java_indexer.decode_source("größe".encode("latin-1")) == "größe"
    every_byte = bytes(range(256))
    assert len(java_indexer.decode_source(every_byte)) == 256  # every byte is a character
    assert java_indexer.decode_source(b"\xef\xbb\xbfclass A {}") == "class A {}"  # (a UTF-8 mark is not part of the first name)
    assert java_indexer.decode_source("class A {}".encode("utf-16-le")) == "class A {}"
    assert java_indexer.decode_source(b"class A {\x00}") == "class A {\x00}"  # (a stray NUL is part of the text)


# ---- a file or a directory the walk cannot open ----------------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permissions do not stop root")
def test_a_file_that_cannot_be_opened_may_declare_anything_and_is_listed(make_repo, tmp_path):
    root = make_repo({**MARKERS, **BASE})
    (tmp_path / "target").mkdir()
    locked = tmp_path / "target" / "Locked.java"
    locked.write_text("package q;\nclass Locked {}\n")
    locked.chmod(0)
    try:
        with deadline():
            g = build_graph(root, JAVA)
    finally:
        locked.chmod(0o644)
    assert EDGE not in edges(g)
    assert [e for e in g.parse_errors if "Locked.java" in e and "could not be read" in e]


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permissions do not stop root")
def test_a_directory_that_cannot_be_listed_may_hold_any_source_and_is_listed(make_repo, tmp_path):
    root = make_repo({**MARKERS, **BASE})
    closed = tmp_path / "target" / "closed"
    closed.mkdir(parents=True)
    closed.chmod(0)
    try:
        with deadline():
            g = build_graph(root, JAVA)
    finally:
        closed.chmod(0o755)
    assert EDGE not in edges(g)
    assert [e for e in g.parse_errors if "closed" in e and "cannot be listed" in e]


def test_read_unread_source_says_what_each_kind_of_name_is(tmp_path):
    (tmp_path / "ok.java").write_text("class A {}\n")
    os.symlink(tmp_path / "ok.java", tmp_path / "inside.java")
    os.symlink(tmp_path / "missing.java", tmp_path / "dangling.java")
    (tmp_path / "dir.java").mkdir()
    real = os.path.realpath(tmp_path)
    assert read_unread_source(str(tmp_path / "ok.java"), real) == ("ok", b"class A {}\n")
    assert read_unread_source(str(tmp_path / "inside.java"), real) == ("ok", b"class A {}\n")
    assert read_unread_source(str(tmp_path / "dangling.java"), real) == ("absent", b"")
    assert read_unread_source(str(tmp_path / "dir.java"), real) == ("absent", b"")
    assert read_unread_source(str(tmp_path / "nothing.java"), real) == ("absent", b"")
    outer = tmp_path.parent / (tmp_path.name + "-far.java")
    outer.write_text("class Far {}\n")
    os.symlink(outer, tmp_path / "far.java")
    assert read_unread_source(str(tmp_path / "far.java"), real) == ("outside", b"")
    assert read_unread_source(str(tmp_path / "far.java"), None)[0] == "ok"  # (no repo to be outside of: a direct call)
