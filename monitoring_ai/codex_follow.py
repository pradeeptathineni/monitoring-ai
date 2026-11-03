"""Follow one selected Codex file without assuming a rotated file is continuous."""

from .adapters import codex, codex_row
from .claude_follow import NativeFollower


class CodexFollower(NativeFollower):
    shape = staticmethod(codex_row)
    supports_turns = True

    def _convert(self, rows):
        return codex(rows, self.source, self.project)

    def turns(self):
        from .codex_turns import turn_spans
        return turn_spans(self._rows, self.source, self.project)

    def _validate_generation(self, events):
        # A replacement/truncation must replay all accepted deltas in order.
        # A partial rotated segment cannot reconstruct safe cumulative deltas.
        if self._events and events[:len(self._events)] != self._events:
            raise ValueError("Codex replacement does not replay the accepted prefix")
