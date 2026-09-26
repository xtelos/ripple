"""Business logic the handlers call into."""


class UserDirectory:
    def __init__(self):
        self._users = {"ada": {"name": "Ada"}}

    def find(self, username):
        return self._users.get(username)


DIRECTORY = UserDirectory()


def greeting_for(username):
    user = DIRECTORY.find(username)
    return f"Hello, {user['name']}" if user else "Hello, stranger"
