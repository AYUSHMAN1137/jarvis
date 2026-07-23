"""Phase 3 — Context Engine package.

Grounding layer: vague references ("isko band karo", "wo report bhejo",
"pehla wala kholo") ko concrete entities se jodta hai, Watcher + Memory +
conversation + tool-results ko mila ke.

Fail-soft: kuch bhi toote to caller apne purane behaviour pe chalta rahe.
"""

from app.services.context.context_engine import (
    ContextEntity,
    ContextRegistry,
    ResolveResult,
    AliasStore,
    detect_reference,
    infer_types_from_text,
    parse_ordinal,
    build_registry,
)

__all__ = [
    "ContextEntity",
    "ContextRegistry",
    "ResolveResult",
    "AliasStore",
    "detect_reference",
    "infer_types_from_text",
    "parse_ordinal",
    "build_registry",
]
