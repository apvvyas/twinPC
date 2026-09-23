from dataclasses import dataclass


@dataclass
class Unsupported:
    """What adapters.get() returns for a platform value twinpc cannot handle (yet)."""
    kind: str
    value: str
    planned: bool

    def reason(self):
        what = f"{self.kind} '{self.value or 'unknown'}'"
        return f"{what} is not supported yet (planned)" if self.planned else f"{what} is not supported"
