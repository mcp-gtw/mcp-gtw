from __future__ import annotations

from dataclasses import dataclass

import mcp.types as types
from jsonschema import Draft202012Validator


@dataclass(slots=True)
class CompiledTool:
    """A tool definition paired with its pre-compiled input and output validators."""

    definition: types.Tool
    input_validator: Draft202012Validator
    output_validator: Draft202012Validator | None
