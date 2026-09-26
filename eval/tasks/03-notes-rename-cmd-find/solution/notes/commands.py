"""One function per subcommand. The CLI looks them up by name."""

from .helpers import slugify
from .helpers import truncate as short
from .store import NoteStore


def cmd_add(store: NoteStore, args):
    path = store.add(args.title, args.body)
    print(f"saved {path.name}")


def cmd_list(store: NoteStore, args):
    for title in store.list_titles():
        print(short(title))


def cmd_search(store, args):
    for title in store.search(slugify(args.needle)):
        print(title)


COMMANDS = {"add": cmd_add, "list": cmd_list, "find": cmd_search}
