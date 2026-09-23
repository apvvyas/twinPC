import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))
from twinpc_lib.steps import Result  # noqa: E402


class FakeRunner:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def run(self, machine, cmd, root=False, input=None):
        self.calls.append((machine, cmd, root, input))
        for m, sub, rc, out in self.responses:
            if m == machine and sub in cmd:
                return Result(rc, out)
        return Result(1, "")
