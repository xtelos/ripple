"""Notes live as markdown files in one directory."""

from pathlib import Path

from notes import helpers as h


class NoteStore:
    def __init__(self, root):
        self.root = Path(root)

    def add(self, title, body):
        path = self.root / h.note_filename(title)
        path.write_text(body)
        return path

    def list_titles(self):
        return sorted(p.stem for p in self.root.glob("*.md"))

    def search(self, needle):
        return [t for t in self.list_titles() if needle in t]
