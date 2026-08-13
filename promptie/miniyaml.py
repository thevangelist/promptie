"""A deliberately small YAML subset reader.

Zero dependencies is a design constraint here: the generated runtime is plain
bash, and the generator should not need a virtualenv to run. We therefore
support only what a persona definition actually needs:

    key: scalar
    key: |          (literal block, newlines kept)
    key: >          (folded block, newlines become spaces)
    key:
      - list item
    key:
      nested: scalar
    key:
      - name: x     (list of mappings, one level)
        test: y

Anything beyond that raises, loudly, rather than silently mis-parsing.
"""

from typing import Any, Dict, List, Tuple


class YamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1].isspace()):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if text == "null" or text == "":
        return ""
    return text


def _lines(text: str) -> List[Tuple[int, str, int]]:
    """(indent, content, line_number) for every significant line."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        out.append((len(stripped) - len(stripped.lstrip()), stripped.strip(), n))
    return out


def loads(text: str) -> Dict[str, Any]:
    raw_lines = text.splitlines()
    items = _lines(text)
    result, _ = _parse_block(items, 0, 0, raw_lines)
    if not isinstance(result, dict):
        raise YamlError("top level of a persona definition must be a mapping")
    return result


def _block_scalar(raw_lines: List[str], start_line: int, style: str) -> Tuple[str, int]:
    """Read an indented literal/folded block starting after start_line (1-based)."""
    collected, i = [], start_line  # start_line is the 'key: |' line, 0-based index next
    base = None
    while i < len(raw_lines):
        line = raw_lines[i]
        if not line.strip():
            collected.append("")
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            base = indent
        if indent < base:
            break
        collected.append(line[base:])
        i += 1
    while collected and not collected[-1].strip():
        collected.pop()
    if style == "|":
        return "\n".join(collected), i
    # folded: blank lines become paragraph breaks, everything else joins
    paragraphs, current = [], []
    for line in collected:
        if line.strip():
            current.append(line.strip())
        else:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs), i


def _parse_block(items, pos, indent, raw_lines):
    """Parse items[pos:] at the given indent. Returns (value, next_pos)."""
    if pos >= len(items):
        return "", pos

    if items[pos][1].startswith("- "):
        return _parse_list(items, pos, indent, raw_lines)

    mapping: Dict[str, Any] = {}
    while pos < len(items):
        item_indent, content, lineno = items[pos]
        if item_indent < indent:
            break
        if item_indent > indent:
            raise YamlError("line %d: unexpected indentation" % lineno)
        if ":" not in content:
            raise YamlError("line %d: expected 'key: value', got %r" % (lineno, content))

        key, _, rest = content.partition(":")
        key, rest = key.strip(), rest.strip()

        if rest in ("|", ">"):
            value, consumed_to = _block_scalar(raw_lines, lineno, rest)
            mapping[key] = value
            pos += 1
            while pos < len(items) and items[pos][2] <= consumed_to:
                pos += 1
            continue

        if rest:
            mapping[key] = _scalar(rest)
            pos += 1
            continue

        # Nested block: look ahead for its indent.
        if pos + 1 < len(items) and items[pos + 1][0] > item_indent:
            value, pos = _parse_block(items, pos + 1, items[pos + 1][0], raw_lines)
            mapping[key] = value
        else:
            mapping[key] = ""
            pos += 1
    return mapping, pos


def _parse_list(items, pos, indent, raw_lines):
    out: List[Any] = []
    while pos < len(items):
        item_indent, content, lineno = items[pos]
        if item_indent < indent or not content.startswith("- "):
            break
        body = content[2:].strip()
        # A list of mappings: "- name: x" followed by deeper "test: y" lines.
        if ":" in body:
            key, _, rest = body.partition(":")
            if rest.strip() or (pos + 1 < len(items) and items[pos + 1][0] > item_indent):
                entry = {key.strip(): _scalar(rest)}
                pos += 1
                while pos < len(items) and items[pos][0] > item_indent and not items[pos][1].startswith("- "):
                    k2, _, v2 = items[pos][1].partition(":")
                    entry[k2.strip()] = _scalar(v2)
                    pos += 1
                out.append(entry)
                continue
        out.append(_scalar(body))
        pos += 1
    return out, pos


def load_file(path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())
