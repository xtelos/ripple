"""Argument parsing and dispatch to the command functions."""

import argparse
import sys

from notes import commands
from notes.commands import COMMANDS
from notes.store import NoteStore


def build_parser():
    parser = argparse.ArgumentParser(prog="notes")
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("body")
    sub.add_parser("list")
    find = sub.add_parser("find")
    find.add_argument("needle")
    return parser


def run(argv=None):
    args = build_parser().parse_args(argv)
    store = NoteStore(args.root)
    handler = COMMANDS[args.command]
    handler(store, args)
    return 0


def run_named(name, store, args):
    """Scripting entry point: run a command by name without argv parsing."""
    fn = getattr(commands, "cmd_" + name)
    return fn(store, args)


def main():
    sys.exit(run())
