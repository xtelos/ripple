"""A repo's own files cannot hang, crash or steer the check: class files, sources and reports are read as bounded regular files.

The check reads these AFTER Maven, inside the repo's lock, so one file that blocks a read (a FIFO named X.class) would make
every later check of the repo wait out its limit. The objects here are real ones under tmp_path (a FIFO, links to /dev/zero
and to a file outside the repo), every call runs under a deadline, and nothing may raise out of `judge`.
"""

from __future__ import annotations

import os
import random
import signal
import struct

import pytest
from java_check_support import CAPTURES, deadline, run_of

from ripple import java_check as jc

pytestmark = pytest.mark.skipif(not (hasattr(os, "mkfifo") and hasattr(signal, "SIGALRM")), reason="needs FIFOs and SIGALRM")

GOOD = CAPTURES / "pass" / "classes" / "_" / "test-classes" / "com" / "acme" / "shop" / "CartTests.class"


def header(count: int) -> bytes:
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + struct.pack(">H", count)


# a Class entry whose name index points at another Class entry, not at a Utf8
CLASS_LOOP = header(4) + b"\x07\x00\x02\x07\x00\x03\x01\x00\x03a/B" + b"\x00\x21\x00\x01\x00\x00" + b"\x00\x00" * 4


def class_dir(root, kind="test-classes"):
    directory = root / "m" / "target" / kind / "p"
    directory.mkdir(parents=True)
    return directory


def read_all(root):
    with deadline(10):
        return {c.relative.rpartition("/")[2]: c for c in jc.compiled_classes(root)}


def test_a_fifo_named_like_a_class_is_not_read_and_does_not_block(tmp_path):
    os.mkfifo(class_dir(tmp_path) / "X.class")
    found = read_all(tmp_path)
    assert found["X.class"].info is None


def test_a_link_to_dev_zero_is_not_read(tmp_path):
    (class_dir(tmp_path) / "Z.class").symlink_to("/dev/zero")
    assert read_all(tmp_path)["Z.class"].info is None


def test_a_link_to_a_class_outside_the_repo_is_not_followed(tmp_path):
    outside = tmp_path / "outside" / "Out.class"
    outside.parent.mkdir()
    outside.write_bytes(GOOD.read_bytes())
    repo = tmp_path / "repo"
    repo.mkdir()
    (class_dir(repo) / "Out.class").symlink_to(outside)
    assert read_all(repo)["Out.class"].info is None


def test_a_classes_directory_that_is_a_link_out_of_the_repo_is_not_walked(tmp_path):
    outside = tmp_path / "outside"
    (outside / "p").mkdir(parents=True)
    (outside / "p" / "Out.class").write_bytes(GOOD.read_bytes())
    repo = tmp_path / "repo"
    (repo / "m" / "target").mkdir(parents=True)
    (repo / "m" / "target" / "test-classes").symlink_to(outside)
    assert read_all(repo) == {}


def test_a_class_file_over_the_size_cap_is_not_read_and_one_at_the_cap_is(tmp_path, monkeypatch):
    data = GOOD.read_bytes()
    directory = class_dir(tmp_path)
    (directory / "Big.class").write_bytes(data + b"\0")
    (directory / "Fits.class").write_bytes(data)
    monkeypatch.setattr(jc, "MAX_CLASS_BYTES", len(data))
    found = read_all(tmp_path)
    assert found["Big.class"].info is None and found["Fits.class"].info is not None


def test_a_read_is_never_longer_than_its_cap(tmp_path):
    path = tmp_path / "long.bin"
    path.write_bytes(b"x" * 1000)
    with deadline(10):
        with pytest.raises(OSError):
            jc.read_regular(path, tmp_path, 64)
        assert len(jc.read_regular(path, tmp_path, 1000)) == 1000


def test_read_regular_reads_a_regular_file_and_refuses_anything_else(tmp_path):
    inside = tmp_path / "a.bin"
    inside.write_bytes(b"abc")
    outside = tmp_path.parent / f"{tmp_path.name}-out.bin"
    outside.write_bytes(b"abc")
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "link-out").symlink_to(outside)
    (tmp_path / "link-in").symlink_to(inside)
    with deadline(10):
        assert jc.read_regular(inside, tmp_path, 10) == b"abc"
        for refused in ("fifo", "link-out", "link-in", "missing"):
            with pytest.raises(OSError):
                jc.read_regular(tmp_path / refused, tmp_path, 10)
        with pytest.raises(OSError):
            jc.read_regular(tmp_path, tmp_path, 10)  # a directory
        with pytest.raises(OSError):
            jc.read_regular(outside, tmp_path, 10)  # a regular file, but not inside root
        assert jc.read_regular(tmp_path / "link-in", tmp_path, 10, follow=True) == b"abc"
        with pytest.raises(OSError):
            jc.read_regular(tmp_path / "link-out", tmp_path, 10, follow=True)


def test_a_class_entry_that_names_another_class_entry_is_not_a_class_and_does_not_crash():
    assert jc.read_class(CLASS_LOOP) is None


@pytest.mark.parametrize(
    "data",
    [
        header(0),
        header(65535),  # a constant pool as long as a u2 allows, with nothing behind it
        header(65535) + b"\x01\xff\xff" + b"x" * 100,
        header(3) + b"\x01\x00\x01a\x07\x00\x01" + b"\x00\x21\x00\x02\x00\x00" + b"\x00\x00" * 3 + b"\x00\x01" + b"\x00\x01\xff\xff\xff\xff",  # an attribute of 4 GB
    ],
)
def test_the_reader_is_bounded_by_the_file_it_reads(data):
    with deadline(5):
        assert jc.read_class(data) is None


def test_a_class_file_changed_anywhere_is_a_class_or_not_and_never_an_error():
    whole = GOOD.read_bytes()
    rng = random.Random(7)
    with deadline(30):
        for _ in range(3000):
            data = bytearray(whole)
            for _ in range(rng.randint(1, 6)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            jc.read_class(bytes(data))


def test_a_source_a_report_that_blocks_a_read_is_not_read(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    fifo = repo / "src" / "test" / "java" / "com" / "acme" / "shop" / "Pipe.java"
    os.mkfifo(fifo)
    reports = repo / "target" / "surefire-reports"
    os.mkfifo(reports / "TEST-com.acme.shop.Pipe.xml")
    with deadline(10):
        assert jc.read_java_file(fifo) is None
        cases, reason = jc.read_report(repo, project, "target/surefire-reports/TEST-com.acme.shop.Pipe.xml")
    assert cases == [] and reason is not None


def test_hostile_class_files_in_a_whole_run_are_a_note_and_the_verdict_still_comes(tmp_path):
    repo, project, run = run_of(tmp_path, "pass")
    classes = repo / "target" / "test-classes" / "com" / "acme" / "shop"
    os.mkfifo(classes / "Fifo.class")
    (classes / "Zero.class").symlink_to("/dev/zero")
    (classes / "Loop.class").write_bytes(CLASS_LOOP)
    (classes / "Half.class").write_bytes(GOOD.read_bytes()[:200])
    with deadline(30):
        result = jc.judge(repo, project, [run], "mvn", 0.1, 300)
    assert result["passed"] is True, result["problems"]
    assert any("4 compiled class files could not be read" in n for n in result["notes"]), result["notes"]
    named = " ".join(result["notes"])
    assert "Fifo.class" in named and "Zero.class" in named and "Loop.class" in named and "Half.class" in named
