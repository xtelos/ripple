"""Node repositories in the eval's repo registry and fetcher (no network, no npm).

A node entry in eval/repos.json is a pinned checkout plus a vendored package-lock.json;
`python -m eval.fetch` installs it with `npm ci --ignore-scripts` into a read-only cache
directory outside the checkout, and a working copy gets node_modules as a symlink to it.
These tests fake the one command that needs the network (`npm ci`).
"""

import dataclasses
import json
import os
import shutil
import stat

import pytest

import eval.fetch as fetch
from eval.loader import copy_fixture
from eval.repos import NotNodeRepo, NotPythonRepo, RepoSpec, load_repos

LOCK = '{"name": "demo", "lockfileVersion": 3, "packages": {}}\n'
PACKAGE_JSON = '{"name": "demo", "version": "1.0.0", "devDependencies": {}}\n'


def a_node_spec(tmp_path, name="demo", runner="vitest", lock=LOCK, **extra):
    lockfile = tmp_path / "lockfiles" / name / "package-lock.json"
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    lockfile.write_text(lock)
    return RepoSpec(
        name=name,
        url="https://example.invalid/demo.git",
        sha="b" * 40,
        describe="",
        python="",
        pythonpath=(),
        tests="tests",
        deps=(),
        runtime="node",
        manager="npm",
        runner=runner,
        lockfile=lockfile,
        **extra,
    )


def node_entry(**over):
    data = {
        "runtime": "node",
        "url": "https://example.invalid/demo.git",
        "sha": "c" * 40,
        "manager": "npm",
        "runner": "vitest",
        "tests": "tests",
        "lockfile": "lockfiles/demo/package-lock.json",
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def load_one(tmp_path, data, name="demo"):
    (tmp_path / "lockfiles" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lockfiles" / "demo" / "package-lock.json").write_text(LOCK)
    (tmp_path / "repos.json").write_text(json.dumps({name: data}))
    return load_repos(tmp_path / "repos.json")[name]


def test_a_node_entry_loads_with_the_lockfile_resolved_next_to_the_repos_file(tmp_path):
    spec = load_one(tmp_path, node_entry(ignore=["tests/slow.test.ts"], config="test/jest.config.js", runner="jest"))
    assert (spec.runtime, spec.manager, spec.runner, spec.config) == ("node", "npm", "jest", "test/jest.config.js")
    assert spec.lockfile == tmp_path / "lockfiles" / "demo" / "package-lock.json"
    assert spec.ignore == ("tests/slow.test.ts",) and spec.deps == () and spec.python == ""


@pytest.mark.parametrize(
    "change, message",
    [
        ({"runtime": "deno"}, 'runtime must be "python" or "node"'),
        ({"runner": "mocha"}, "runner must be vitest or jest"),
        ({"runner": None}, "demo is missing runner"),
        ({"lockfile": None}, "demo is missing lockfile"),
        ({"manager": None}, "demo is missing manager"),
        ({"manager": "yarn"}, "manager must be npm"),
        ({"tests": None}, "demo is missing tests"),
        ({"sha": "main"}, "40-character commit SHA"),
        ({"lockfile": "lockfiles/demo/nope.json"}, "lockfile .* is not a file"),
        ({"lockfile": "../outside/package-lock.json"}, "lockfile must stay inside"),
        ({"lockfile": "/etc/passwd"}, "lockfile must stay inside"),
        ({"config": "../jest.config.js"}, "config must be a relative path"),
        ({"tests": "../elsewhere"}, "tests must be a relative path"),
        ({"deps": ["pytest==8.4.2"]}, "deps is a python setting"),
        ({"python": "3.12"}, "python is a python setting"),
        ({"pythonpath": ["src"]}, "pythonpath is a python setting"),
        ({"deselect": ["a b"]}, "deselect is a python setting"),
        ({"unparseable": ["x.py"]}, "unparseable is a python setting"),
        ({"ignore": "tests/x.ts"}, "ignore must be a list"),
        ({"ignore": ["tests/a,b.test.ts"]}, "ignore entries may not contain a comma"),
    ],
)
def test_a_bad_node_entry_is_refused_with_a_message_that_names_the_fault(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_one(tmp_path, node_entry(**change))


@pytest.mark.parametrize("key, value", [("runner", "vitest"), ("manager", "npm"), ("lockfile", "x"), ("config", "x")])
def test_a_python_entry_may_not_carry_node_settings(tmp_path, key, value):
    data = {"url": "u", "sha": "a" * 40, "python": "3.12", "tests": "tests", "deps": [], key: value}
    (tmp_path / "repos.json").write_text(json.dumps({"r": data}))
    with pytest.raises(ValueError, match=f"{key} is a node setting"):
        load_repos(tmp_path / "repos.json")


def test_python_entries_keep_the_python_runtime_when_runtime_is_not_written(tmp_path):
    data = {"url": "u", "sha": "a" * 40, "python": "3.12", "tests": "tests", "deps": ["pytest==8.4.2"]}
    (tmp_path / "repos.json").write_text(json.dumps({"r": data, "s": {**data, "runtime": "python"}}))
    repos = load_repos(tmp_path / "repos.json")
    assert repos["r"].runtime == repos["s"].runtime == "python"
    assert repos["r"] == dataclasses.replace(repos["s"], name="r")


def test_the_deps_key_follows_the_lockfile_content_and_nothing_else(tmp_path):
    spec = a_node_spec(tmp_path)
    key = spec.deps_dir.name
    assert spec.deps_dir.parent.name == "deps" and key.startswith("demo-")
    # same bytes, a rewritten file: same key
    spec.lockfile.write_text(LOCK)
    assert spec.deps_dir.name == key
    # a different checkout or a different runner reads the same installed packages
    assert RepoSpec(**{**spec.__dict__, "sha": "d" * 40, "runner": "jest"}).deps_dir == spec.deps_dir
    # one changed package version: a new directory, the old one is left where it was
    spec.lockfile.write_text(LOCK.replace('"packages": {}', '"packages": {"": {"version": "2"}}'))
    assert spec.deps_dir.name != key
    # and the name still separates two repos that happen to lock the same bytes
    other = a_node_spec(tmp_path, name="other")
    assert other.deps_dir != spec.deps_dir and other.deps_dir.name.startswith("other-")


def test_the_deps_key_does_not_need_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    assert not spec.checkout.exists()
    assert spec.deps_dir.parent == tmp_path / "cache" / "deps"
    assert spec.node_modules == spec.deps_dir / "node_modules"
    assert not spec.deps_dir.is_relative_to(spec.checkout) and not spec.checkout.is_relative_to(spec.deps_dir)


def test_node_available_needs_the_checkout_and_the_ready_marker_not_just_the_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    assert not spec.available
    spec.checkout.mkdir(parents=True)
    assert not spec.available  # source but no packages
    spec.node_modules.mkdir(parents=True)
    assert not spec.available  # a node_modules directory is what a half-finished npm ci leaves too
    spec.deps_ready.write_text("done\n")
    assert spec.available
    shutil.rmtree(spec.checkout)
    assert not spec.available


def test_python_only_properties_refuse_a_node_spec_rather_than_return_python_paths(tmp_path):
    spec = a_node_spec(tmp_path)
    for attr in ("venv", "venv_python", "venv_ready"):
        with pytest.raises(NotPythonRepo, match="demo is a node repo"):
            getattr(spec, attr)
    py = RepoSpec(name="p", url="u", sha="a" * 40, describe="", python="3.12", pythonpath=(), tests="tests", deps=())
    for attr in ("deps_dir", "node_modules", "deps_ready", "lockfile_in_repo"):
        with pytest.raises(NotNodeRepo, match="p is a python repo"):
            getattr(py, attr)
    with pytest.raises(NotNodeRepo, match="p is a python repo"):
        py.node_argv()


def test_node_env_puts_the_repos_own_binaries_first_and_keeps_the_python_contract_separate(tmp_path):
    spec = a_node_spec(tmp_path)
    repo = tmp_path / "work"
    env = spec.env(repo, path="/usr/bin")
    assert env == {"PATH": f"{repo}/node_modules/.bin{os.pathsep}/usr/bin"}


@pytest.mark.parametrize(
    "runner, config, ignore, expected",
    [
        ("vitest", "", (), ["vitest", "run", "--no-cache", "tests"]),
        ("vitest", "", ("tests/a.test.ts",), ["vitest", "run", "--no-cache", "--exclude", "tests/a.test.ts", "tests"]),
        ("vitest", "", ("tests/a.test.ts", "tests/b/**"), ["vitest", "run", "--no-cache", "--exclude", "{tests/a.test.ts,tests/b/**}", "tests"]),
        ("jest", "test/jest.config.js", (), ["jest", "--config", "test/jest.config.js", "tests"]),
    ],
)
def test_the_runner_command_for_each_runner(tmp_path, runner, config, ignore, expected):
    spec = a_node_spec(tmp_path, runner=runner, config=config, ignore=ignore)
    argv = spec.node_argv()
    assert argv == expected
    assert spec.node_argv(("tests/x.test.ts",))[-1] == "tests/x.test.ts"
    assert "tests" not in spec.node_argv(("tests/x.test.ts",))  # an explicit target replaces the default one


def test_jest_ignore_leaves_the_repos_own_ignore_list_alone_and_refuses_the_paths_in_the_target(tmp_path):
    import re

    spec = a_node_spec(tmp_path, runner="jest", ignore=("src/__tests__/a.test.ts", "src/__tests__/b.tests.ts"))
    argv = spec.node_argv()
    assert not any(arg.startswith("--testPathIgnorePatterns") for arg in argv)  # that flag would replace the config's list
    pattern = re.compile(argv[-1])
    root = "/work/repo/"
    assert pattern.search(root + "tests/c.test.ts")
    assert not pattern.search(root + "src/__tests__/a.test.ts") and not pattern.search(root + "src/__tests__/b.tests.ts")
    assert not re.compile(spec.node_argv(("src/__tests__",))[-1]).search(root + "src/__tests__/a.test.ts")
    assert re.compile(spec.node_argv(("src/__tests__",))[-1]).search(root + "src/__tests__/c.test.ts")


def fake_npm(spec, fail=False, calls=None):
    def fake_run(cmd, cwd=None):
        if calls is not None:
            calls.append(cmd)
        if cmd[:2] == ["npm", "ci"]:
            assert "--ignore-scripts" in cmd, "package lifecycle scripts must never run"
            assert (cwd / "package.json").is_file() and (cwd / "package-lock.json").read_text() == spec.lockfile.read_text()
            (cwd / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
            (cwd / "node_modules" / "pkg").mkdir(exist_ok=True)
            (cwd / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
            if fail:
                raise RuntimeError("network dropped")
        return ""

    return fake_run


def a_checkout(spec):
    spec.checkout.mkdir(parents=True)
    (spec.checkout / "package.json").write_text(PACKAGE_JSON)


def test_deps_are_installed_outside_the_checkout_then_made_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    a_checkout(spec)
    calls = []
    monkeypatch.setattr(fetch, "run", fake_npm(spec, calls=calls))
    fetch.build_deps(spec)
    assert spec.available and spec.deps_ready.is_file()
    assert [c[:2] for c in calls] == [["npm", "ci"]]
    assert not (spec.checkout / "node_modules").exists()
    assert not os.access(spec.node_modules / "pkg" / "index.js", os.W_OK)  # a file inside is not writable
    assert not os.access(spec.node_modules / "pkg", os.W_OK)  # nor can one be added or removed
    assert not os.access(spec.deps_dir, os.W_OK)
    assert stat.S_IMODE((spec.node_modules / "pkg" / "index.js").stat().st_mode) & 0o444  # still readable
    calls.clear()
    fetch.build_deps(spec)  # re-running a finished install is a no-op
    assert calls == []


def test_an_install_that_failed_is_not_available_and_the_next_fetch_starts_over(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    a_checkout(spec)
    monkeypatch.setattr(fetch, "run", fake_npm(spec, fail=True))
    with pytest.raises(RuntimeError, match="network dropped"):
        fetch.build_deps(spec)
    assert spec.node_modules.is_dir() and not spec.available  # packages on disk, no marker: not finished
    calls = []
    monkeypatch.setattr(fetch, "run", fake_npm(spec, calls=calls))
    fetch.build_deps(spec)
    assert spec.available and [c[:2] for c in calls] == [["npm", "ci"]]


def test_a_half_locked_directory_from_an_interrupted_fetch_is_rebuilt(tmp_path, monkeypatch):
    """Interrupted after some chmod and before the marker: nothing there is writable, and
    the next fetch must still be able to remove it and install again."""
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    a_checkout(spec)
    (spec.node_modules / "pkg").mkdir(parents=True)
    (spec.node_modules / "pkg" / "old.js").write_text("old\n")
    for path in (spec.node_modules / "pkg" / "old.js", spec.node_modules / "pkg", spec.node_modules, spec.deps_dir):
        path.chmod(0o555 if path.is_dir() else 0o444)
    assert not spec.available
    monkeypatch.setattr(fetch, "run", fake_npm(spec))
    fetch.build_deps(spec)
    assert spec.available and not (spec.node_modules / "pkg" / "old.js").exists()


def test_the_vendored_lockfile_is_copied_into_the_checkout_and_a_different_upstream_one_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    a_checkout(spec)
    fetch.place_lockfile(spec)
    assert (spec.checkout / "package-lock.json").read_text() == LOCK
    fetch.place_lockfile(spec)  # the same bytes again: fine
    (spec.checkout / "package-lock.json").write_text('{"other": true}\n')
    with pytest.raises(RuntimeError, match="differs from eval/lockfiles"):
        fetch.place_lockfile(spec)


def test_a_working_copy_gets_node_modules_as_a_link_and_never_a_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    a_checkout(spec)
    (spec.checkout / "tests").mkdir()
    (spec.checkout / "tests" / "a.test.ts").write_text("test('a', () => {})\n")
    (spec.checkout / "node_modules").mkdir()  # even a checkout that somehow has one
    (spec.checkout / "node_modules" / "stray.js").write_text("stray\n")
    monkeypatch.setattr(fetch, "run", fake_npm(spec))
    fetch.build_deps(spec)

    from eval.loader import Task

    task = Task(id="t", fixture=spec.checkout, prompt="p", min_tests=1, why="w", dir=tmp_path, spec=spec)
    work = copy_fixture(task, tmp_path / "work")
    link = work / "node_modules"
    assert link.is_symlink() and link.resolve() == spec.node_modules.resolve()
    assert (work / "tests" / "a.test.ts").is_file() and not (link / "stray.js").exists()
    assert not os.access(link / "pkg" / "index.js", os.W_OK)


def test_linking_deps_refuses_a_real_directory_and_a_dependency_set_that_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("RIPPLE_EVAL_CACHE", str(tmp_path / "cache"))
    spec = a_node_spec(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(FileNotFoundError, match="python -m eval.fetch"):
        spec.link_deps(work)  # a link to nothing would fail later and confusingly
    a_checkout(spec)
    monkeypatch.setattr(fetch, "run", fake_npm(spec))
    fetch.build_deps(spec)
    (work / "node_modules").mkdir()
    with pytest.raises(FileExistsError, match="node_modules"):
        spec.link_deps(work)
    (work / "node_modules").rmdir()
    spec.link_deps(work)
    spec.link_deps(work)  # re-linking an existing link is fine
    assert (work / "node_modules").is_symlink()
