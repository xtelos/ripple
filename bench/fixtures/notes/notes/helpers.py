"""Small string and date helpers shared by the commands."""

import re
from datetime import date


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def today_stamp():
    return date.today().isoformat()


def note_filename(title):
    return f"{today_stamp()}-{slugify(title)}.md"


def truncate(text, width=40):
    return text if len(text) <= width else text[: width - 3] + "..."
