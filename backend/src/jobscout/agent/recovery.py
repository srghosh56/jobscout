"""Recover tool calls that a model wrote as text instead of native function calls.

Small local models (e.g. Mistral 7B via Ollama) sometimes emit their native tool-call
JSON (``[{"name": ..., "arguments": {...}}]``) or a Python-like call
(``search_postings(query="...")``) in the message content. We parse those strictly:
only names in the tool registry, arguments must be JSON/literal values, and the
arguments are still validated by the tool's Pydantic schema before execution.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from collections.abc import Iterable
from typing import Any

_PY_CALL = re.compile(r"\b([a-z_]{3,40})\((.*?)\)\s*(?:```|$|\n)", re.S)


PAIRS = {"{": "}", "[": "]"}


def balance_json(s: str) -> str:
    """Insert missing closing brackets (outside strings): small models often drop a
    ``}`` before the final ``]``. Only adds closers; never removes characters."""
    out: list[str] = []
    stack: list[str] = []
    in_str = esc = False
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in PAIRS:
            stack.append(PAIRS[ch])
        elif ch in ("}", "]"):
            while stack and stack[-1] != ch:
                out.append(stack.pop())
            if stack:
                stack.pop()
            else:
                continue  # stray closer: drop
        out.append(ch)
        if not stack and out and out[0] in PAIRS:
            break
    if in_str:
        out.append('"')
    out.extend(reversed(stack))
    return "".join(out)


def _decode_json_prefix(text: str) -> Any | None:
    dec = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"[\[{]", text)][:5]:
        chunk = text[start:]
        for candidate in (chunk, balance_json(chunk)):
            try:
                obj, _ = dec.raw_decode(candidate)
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _as_calls(obj: Any, names: set[str]) -> list[dict[str, Any]]:
    items = obj if isinstance(obj, list) else [obj]
    calls = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or (it.get("function") or {}).get("name")
        args = it.get("arguments", it.get("args", (it.get("function") or {}).get("arguments", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if name in names and isinstance(args, dict):
            calls.append(
                {
                    "name": name,
                    "args": args,
                    "id": f"rec_{uuid.uuid4().hex[:12]}",
                    "type": "tool_call",
                }
            )
    return calls


def _py_calls(text: str, names: set[str]) -> list[dict[str, Any]]:
    calls = []
    for m in _PY_CALL.finditer(text):
        name, argstr = m.group(1), m.group(2)
        if name not in names:
            continue
        try:
            node = ast.parse(f"f({argstr})", mode="eval").body
        except SyntaxError:
            continue
        if not isinstance(node, ast.Call) or node.args:
            continue
        try:
            args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
        except (ValueError, SyntaxError):
            continue
        calls.append(
            {"name": name, "args": args, "id": f"rec_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
        )
    return calls


def recover_tool_calls(content: str, tool_names: Iterable[str]) -> list[dict[str, Any]]:
    names = set(tool_names)
    text = (content or "").replace("[TOOL_CALLS]", " ").strip()
    if not text or not any(n in text for n in names):
        return []
    obj = _decode_json_prefix(text)
    if obj is not None:
        calls = _as_calls(obj, names)
        if calls:
            return calls
    calls = _py_calls(text, names)
    if calls:
        return calls
    return _name_then_json(text, names)


def _name_then_json(text: str, names: set[str]) -> list[dict[str, Any]]:
    """``search_postings {"query": ...}`` or ``Call: sql_query: {...}``."""
    for m in re.finditer(r"\b([a-z_]{3,40})\b\s*[:=]?\s*(?=\{)", text):
        if m.group(1) not in names:
            continue
        obj = _decode_json_prefix(text[m.end() :])
        if isinstance(obj, dict):
            args = obj.get("arguments", obj)
            if isinstance(args, dict):
                return [
                    {
                        "name": m.group(1),
                        "args": args,
                        "id": f"rec_{uuid.uuid4().hex[:12]}",
                        "type": "tool_call",
                    }
                ]
    return []
