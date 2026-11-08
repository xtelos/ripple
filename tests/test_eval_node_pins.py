"""The four node repositories pinned in eval/repos.json for the TypeScript eval tasks."""

import json

from eval.repos import load_repos


def test_the_shipped_node_repos_are_pinned_and_their_lockfiles_are_committed():
    node = {n: r for n, r in load_repos().items() if r.runtime == "node"}
    assert set(node) == {"neverthrow", "tsyringe", "superstruct", "react-pro-sidebar"}
    assert {n: r.runner for n, r in node.items()} == {
        "neverthrow": "vitest",
        "tsyringe": "jest",
        "superstruct": "vitest",
        "react-pro-sidebar": "vitest",
    }
    for spec in node.values():
        assert len(spec.sha) == 40 and spec.lockfile.is_file()
        assert json.loads(spec.lockfile.read_text())["lockfileVersion"] >= 2
