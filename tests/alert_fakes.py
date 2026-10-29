"""In-memory email transport. No provider SDK or network calls."""

from copy import deepcopy


class FakeEmailSender:
    def __init__(self, reject_first=False, raise_first=False):
        self.messages = []
        self.attempts = []
        self.reject_first = reject_first
        self.raise_first = raise_first

    def __call__(self, params):
        self.attempts.append(deepcopy(params))
        if self.raise_first and len(self.attempts) == 1:
            raise RuntimeError("Synthetic provider failure before acceptance")
        if self.reject_first and len(self.attempts) == 1:
            # Exercise the worker's explicit unsuccessful/no-message-ID path.
            # A later call succeeds, including a retry of this same listing.
            return {}
        self.messages.append(deepcopy(params))
        return {"id": f"fake-{len(self.messages)}"}
