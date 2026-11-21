"""Auto-capture: turn Session transcripts and durable records into structured,
pending-review suggestions for the team-context layer (P4).

The pipeline is split by *how* each kind of information is best captured:

* deterministic (zero-model) — layer pointers, artifact/workflow/goal
  references, runtime posture;
* model extraction — semantic knowledge, product references, environment
  keys (with values only when the layer lacks them).

Everything lands in a pending-review area; nothing is auto-promoted into
reviewed team facts. See :mod:`core.capture.models` for the suggestion shape.
"""

from core.capture.models import (
    CapturedSuggestion,
    append_suggestion,
    load_suggestions,
    pending_dir,
    save_suggestions,
)

__all__ = [
    "CapturedSuggestion",
    "append_suggestion",
    "load_suggestions",
    "pending_dir",
    "save_suggestions",
]
