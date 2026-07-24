"""
Central tool registry for the J.A.R.V.I.S agentic system.

This is the heart of the scalable design. Adding a new capability is as simple
as writing a function and decorating it with @tool(...). The registry then:

  1. Builds the OpenAI-style JSON schema the LLM needs (tools list).
  2. Looks up and runs the right function when the LLM asks for a tool call.
  3. Tracks which tools are "dangerous" so the agent loop can ask for
     confirmation before running them.

Design goals:
  - Zero changes to the agent loop when you add the 1st or the 100th tool.
  - Pure-Python, framework-agnostic. Works with the Groq SDK today and any
    OpenAI-compatible endpoint (including a local LLM) tomorrow.
"""

from __future__ import annotations

import inspect
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("J.A.R.V.I.S")

# Map our simple human-friendly param type names to JSON Schema types.
_JSON_TYPES = {
    "string": "string",
    "str": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "array": "array",
    "object": "object",
    "dict": "object",
}


@dataclass
class ToolParam:
    """A single parameter of a tool."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: Optional[List[Any]] = None

    def json_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {
            "type": _JSON_TYPES.get(self.type.lower(), "string"),
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        return schema


@dataclass
class ToolSpec:
    """Full specification of a registered tool."""
    name: str
    description: str
    func: Callable[..., Any]
    params: List[ToolParam] = field(default_factory=list)
    dangerous: bool = False
    # If True, the result is a frontend "action" (e.g. open a URL in the
    # browser) rather than something executed on the server itself.
    category: str = "desktop"  # desktop | system | web | google | content
    risk_level: str = "safe"
    verification: Dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> Dict[str, Any]:
        """Return the tool definition in OpenAI/Groq `tools` format."""
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.params:
            properties[p.name] = p.json_schema()
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class ToolRegistry:
    """Holds all registered tools and runs them on demand."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    # ---- registration -------------------------------------------------- #
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            logger.warning("[TOOLS] Tool '%s' is being overwritten", spec.name)
        self._tools[spec.name] = spec
        logger.info("[TOOLS] Registered tool: %s (%s%s)",
                    spec.name, spec.category, ", dangerous" if spec.dangerous else "")

    # ---- lookups ------------------------------------------------------- #
    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def is_dangerous(self, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec and spec.dangerous)

    def openai_schemas(self) -> List[Dict[str, Any]]:
        """All tool schemas for sending to the LLM."""
        return [spec.to_openai_schema() for spec in self._tools.values()]

    def validate_arguments(self, name: str, arguments: Dict[str, Any]) -> List[str]:
        """Validate required fields, enums, and basic JSON types generically."""
        spec = self._tools.get(name)
        if spec is None:
            return [f"unknown tool '{name}'"]
        values = arguments or {}
        if not isinstance(values, dict):
            return ["arguments must be an object"]
        errors: List[str] = []
        py_types = {
            "string": str, "str": str, "int": int, "integer": int,
            "float": (int, float), "number": (int, float),
            "bool": bool, "boolean": bool, "list": list, "array": list,
            "object": dict, "dict": dict,
        }
        for param in spec.params:
            if param.required and param.name not in values:
                errors.append(f"missing required argument '{param.name}'")
                continue
            if param.name not in values:
                continue
            value = values[param.name]
            expected = py_types.get(param.type.lower())
            # bool is an int subclass; do not accept it for numeric fields.
            if expected and (isinstance(value, bool) and param.type.lower() not in ("bool", "boolean")):
                errors.append(f"'{param.name}' has wrong type")
            elif expected and not isinstance(value, expected):
                errors.append(f"'{param.name}' has wrong type")
            if param.enum and value not in param.enum:
                errors.append(f"'{param.name}' is not an allowed value")
        return errors

    def schema_fingerprint(self, name: str) -> str:
        """Stable fingerprint used to invalidate stale learned executions."""
        spec = self._tools.get(name)
        if spec is None:
            return ""
        payload = {
            "name": spec.name, "dangerous": spec.dangerous,
            "risk_level": spec.risk_level, "category": spec.category,
            "verification": spec.verification,
            "params": [{"name": p.name, "type": p.type, "required": p.required,
                        "enum": p.enum} for p in spec.params],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ---- execution ----------------------------------------------------- #
    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Run a tool by name with the given arguments. Always returns a
        string (the observation that is fed back to the LLM). Never raises:
        errors are converted to readable messages so the agent can recover."""
        spec = self._tools.get(name)
        if not spec:
            return f"ERROR: unknown tool '{name}'."

        arguments = arguments or {}
        # Only pass through arguments the function actually accepts, so a
        # hallucinated extra arg never crashes the call.
        try:
            sig = inspect.signature(spec.func)
            accepted = {
                k: v for k, v in arguments.items() if k in sig.parameters
            }
        except (TypeError, ValueError):
            accepted = arguments

        try:
            result = spec.func(**accepted)
            if result is None:
                return f"Done ({name})."
            return str(result)
        except TypeError as e:
            logger.warning("[TOOLS] Bad arguments for '%s': %s", name, e)
            return f"ERROR: invalid arguments for '{name}': {e}"
        except Exception as e:  # noqa: BLE001 - tools must never crash the loop
            logger.warning("[TOOLS] Tool '%s' failed: %s", name, e)
            return f"ERROR: '{name}' failed: {e}"


# A single shared registry instance for the whole app.
registry = ToolRegistry()


def tool(
    name: str,
    description: str,
    params: Optional[Dict[str, Any]] = None,
    dangerous: bool = False,
    category: str = "desktop",
    risk_level: Optional[str] = None,
    verification: Optional[Dict[str, Any]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a function as an LLM-callable tool.

    Example:
        @tool(
            name="set_volume",
            description="Set the system volume to a percentage.",
            params={"level": {"type": "int", "description": "0-100", "required": True}},
        )
        def set_volume(level: int) -> str:
            ...

    `params` is a dict of {param_name: spec}. Each spec can be:
        - a string (treated as the description, type defaults to string), or
        - a dict with keys: type, description, required, enum.
    """

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_params: List[ToolParam] = []
        for pname, pspec in (params or {}).items():
            if isinstance(pspec, str):
                tool_params.append(ToolParam(name=pname, description=pspec))
            elif isinstance(pspec, dict):
                tool_params.append(
                    ToolParam(
                        name=pname,
                        type=pspec.get("type", "string"),
                        description=pspec.get("description", ""),
                        required=pspec.get("required", True),
                        enum=pspec.get("enum"),
                    )
                )
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                func=func,
                params=tool_params,
                dangerous=dangerous,
                category=category,
                risk_level=(risk_level or ("dangerous" if dangerous else "safe")),
                verification=dict(verification or {}),
            )
        )
        return func

    return _decorator
