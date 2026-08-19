"""Persona definition -> file contents.

Templates use {{TOKEN}} rather than str.format because the generated Python is
full of %s and {}. Substitution is a plain string replace: no template language,
nothing to escape wrong.
"""

import os
import re
from pathlib import Path
from typing import Dict

from . import model
from .model import Persona

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_TOKEN_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


def _render(template: str, values: Dict[str, str]) -> str:
    def sub(match):
        key = match.group(1)
        if key not in values:
            raise KeyError("template referenced unknown token {{%s}}" % key)
        return values[key]
    return _TOKEN_RE.sub(sub, template)


def _read(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def commands(p: Persona, skill_dir: str) -> Dict[str, str]:
    """The exact command lines the skill tells the assistant to run.

    Baked at install time rather than discovered at run time: the interpreter is
    an absolute path, which removes the python/python3 problem on Windows, and the
    skill dir is absolute, which removes $SKILL_DIR guessing across profiles.
    """
    from .install import runtime_python
    exe = runtime_python()
    return {
        "ALLOCATE_CMD": '"%s" "%s/new_note.py"' % (exe, skill_dir),
        "INDEX_CMD": '"%s" "%s/append_index.py"' % (exe, skill_dir),
    }


def tokens(p: Persona, skill_dir: str = "<skill-dir>") -> Dict[str, str]:
    store_abs = p.store_path
    values = dict(commands(p, skill_dir))
    values.update({
        "NAME": p.name,
        "TITLE": p.title,
        "SKILL": p.skill,
        "STORE": p.store,
        "STORE_ABS": store_abs,
        "PERSONA_FILE": os.path.basename(p.path) or (p.name + ".yaml"),
        "DESCRIPTION": p.description(),
        "DURABLE_BAD": p.durable_bad,
        "DURABLE_GOOD": p.durable_good,
        "CAPTURE_WORTHY": "\n".join("- %s" % c for c in p.capture_worthy),
        "SKIP": "\n".join("- %s" % s for s in p.skip),
        "SCOPE_TABLE": "\n".join("- `%s` %s %s" % (s.name, " " * max(1, 11 - len(s.name)),
                                                  " ".join(str(s.test).split()))
                                 for s in p.scope),
        "SCOPE_LIST": "\n".join("- **%s**, %s" % (s.name, s.test) for s in p.scope),
        "SCOPE_NAMES": " | ".join(p.scope_names),
        "SCOPE_CASE": "|".join(p.scope_names),
        "RESTRICTIVE_SCOPE": p.restrictive_scope,
        "ARMING_LINE": arming_line(p),
        "SWEEP_LINE": sweep_line(p),
        # Python-literal forms, so the generated runtime never has to escape prose.
        "ARMING_LINE_PY": repr(arming_line(p)),
        "SWEEP_LINE_PY": repr(sweep_line(p)),
        "SCOPE_PY_LIST": repr(p.scope_names),
        "MAX_PER_DAY": str(p.max_per_day),
        "ICON": p.icon,
        "SENSITIVITY": p.sensitivity,
        "SENSITIVITY_BAR": p.bar,
        "EVIDENCE_TABLE": "\n".join("| `%s` | %s |" % (name, test)
                                    for name, test in model.EVIDENCE),
        "KIND_TABLE": "\n".join("- `%s` %s %s" % (k, " " * (11 - len(k)),
                                                 model.COLLISION_KINDS[k])
                                for k in p.collision_kinds),
        "KIND_NAMES": " | ".join(p.collision_kinds),
        "KIND_PY_LIST": repr(p.collision_kinds),
        "PROMOTABLE_PY_LIST": repr(p.promotable_kinds),
        "EVIDENCE_PY_LIST": repr(list(model.WRITABLE_EVIDENCE)),
        "EVIDENCE_FREE_PY": repr(list(model.FREE_EVIDENCE)),
        "EVIDENCE_REFUSED_PY": repr(list(model.REFUSED_EVIDENCE)),
        "EVIDENCE_NAMES": " | ".join(model.WRITABLE_EVIDENCE),
        "EVIDENCE_FREE": ", ".join(model.FREE_EVIDENCE),
        "DURABLE_CONTRAST": "\n\n".join(
            "> Not \"%s\"\n> but \"%s\"" % (bad, good) for bad, good in p.durable_examples),
    })
    return values


def sweep_line(p: Persona) -> str:
    """Injected at compaction. Names the loss, so the sweep has a reason."""
    return (
        "Context is about to be compacted. Anything this session worked out about %s "
        "and did not write down is lost now -- reasoning that emerged here, not knowledge "
        "you arrived with. Capture it with the %s skill, then continue." % (subject(p), p.skill)
    )


def subject(p: Persona) -> str:
    """What the notes are *about*, in prose.

    Deliberately not the persona name: a persona may be named for its trigger
    (`collision`) rather than its subject, and "a durable collision principle"
    reads as nonsense. The title is the human-facing noun.
    """
    return p.title.lower()


def arming_line(p: Persona) -> str:
    """The SessionStart injection. Two sentences, because it is paid every session."""
    extra = (" " + p.language_hint) if p.language_hint else ""
    return (
        "Capture is armed for %s. When this session yields something durable -- "
        "especially where the human knows something you did not -- use the %s skill "
        "in the moment, and sweep once more before the session ends.%s"
        % (subject(p), p.skill, extra)
    )


def skill_md(p: Persona, skill_dir: str = "<skill-dir>") -> str:
    return _render(_read("SKILL.md.tmpl"), tokens(p, skill_dir))


def new_note_py(p: Persona) -> str:
    return _render(_read("py/new_note.py.tmpl"), tokens(p))


def append_index_py(p: Persona) -> str:
    return _render(_read("py/append_index.py.tmpl"), tokens(p))


def hooks_py(p: Persona) -> str:
    return _render(_read("py/hooks.py.tmpl"), tokens(p))


def store_readme(p: Persona) -> str:
    return _render(_read("store-README.md.tmpl"), tokens(p))


def store_index(p: Persona) -> str:
    return "# %s notes, index\n\nOne line per note, in capture order.\n\n" % p.title
