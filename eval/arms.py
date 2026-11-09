"""The test arms of the agent eval: optional changes to what the agent is told or given, each off by default.

    --forced-use       the with-ripple agent is told to call `callers` on the symbol it changes before editing
    --hard-prompts     a task's `hard_prompt` (task.json; the TypeScript tasks 20 to 28 have one) replaces its prompt:
                       the same task with no paths, no lists or counts of sites and no hint of where uses live
    --restrict-search  both agents lose their search channels (Grep, Glob, web, and the search commands the shell
                       would let through); for a node task the shell also loses `tsc`

Flags combine. With none set a batch is exactly what it was before this module existed (tests/test_eval_arms_pins.py);
the arm of a batch is a short label, recorded in the results' meta and in every run record, so a results file says
which arm it is.

What the real CLI takes (`claude --help`, 2.1.319): `--disallowedTools <tools...>`, "comma or space-separated list of
tool names to deny (e.g. "Bash(git *)" Edit)", the sibling of `--allowedTools`, which the harness already passes comma
joined. The built-in tool names Grep, Glob, WebSearch and WebFetch are in the binary. `LS` is listed too: it is an
older tool name, kept here because denying a name the CLI does not have is harmless. A deny rule beats an allow rule.
"""

from __future__ import annotations

from dataclasses import dataclass

# Appended, as its own paragraph, after the line that says a ripple server is connected: with-ripple only.
FORCED_USE_LINE = "Before editing, call the ripple `callers` tool on the symbol you are changing and use the call sites it returns."

SEARCH_UNAVAILABLE = (
    "The Grep and Glob tools, shell search commands (grep, find) and web access are not available."
)

# The Python tasks' shell, as the restricted preamble names it: the test commands the harness allows, nothing else.
RESTRICTED_PREAMBLE = (
    "The shell is there for running the tests (`pytest`, or `python -m pytest`): do not use it to edit files (sed, "
    f"scripted rewrites). Make every edit with the Edit or Write tool. {SEARCH_UNAVAILABLE}"
)

# Claude Code lets a shell command through without a rule when it only reads, and its own list of those names grep,
# egrep, fgrep, rg, ag, find, fd, tree and locate (found in the 2.1.319 binary). Denying the Grep and Glob tools alone
# would leave the shell as a search channel, so these are denied as well; a deny rule wins over the allow rules.
SEARCH_COMMANDS = (
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "find", "fd", "tree", "locate", "git grep", "git ls-files",
    "sed", "awk", "gawk", "xargs",
)  # fmt: skip
RESTRICTED_DISALLOWED = (
    "Grep", "Glob", "LS", "WebSearch", "WebFetch", *(f"Bash({name}:*)" for name in SEARCH_COMMANDS),
)  # fmt: skip


@dataclass(frozen=True)
class Arms:
    forced_use: bool = False
    hard_prompts: bool = False
    restrict_search: bool = False

    @property
    def label(self) -> str:
        """`base`, or the flags that are on in a fixed order, joined with `+`: forced, hard, restricted."""
        flags = (("forced", self.forced_use), ("hard", self.hard_prompts), ("restricted", self.restrict_search))
        return "+".join(name for name, on in flags if on) or "base"

    @property
    def flags(self) -> dict:
        return {"forced_use": self.forced_use, "hard_prompts": self.hard_prompts, "restrict_search": self.restrict_search}


def task_prompt(task, arms: Arms | None = None) -> str:
    """What the task asks: its hard prompt under --hard-prompts if it has one, else its own prompt."""
    return task.hard_prompt if arms is not None and arms.hard_prompts and task.hard_prompt else task.prompt


def arms_of(args) -> Arms:
    """The arms a batch runs with, as main() leaves them on `args`; a namespace without them (a test's) is the base."""
    return getattr(args, "arms", None) or Arms()
