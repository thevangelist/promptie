"""The persona definition: what a domain must declare to be compiled.

Everything the mechanism needs is here, and nothing else is. If a field is not
needed to emit the skill, the hooks, the scripts, the permissions or the store,
it does not belong in the format.
"""

import os
import re
from typing import Any, Dict, List

from . import miniyaml

SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# The skill description rides in context on every single turn of every session.
# This is the one hard budget in the system, so it is enforced, not suggested.
DESCRIPTION_BUDGET_CHARS = 500

# What kind of collision produced the note. Chosen at write time, because nobody
# will ever reclassify a thousand notes afterwards -- and because the index is
# only browsable if the kind is on the line.
COLLISION_KINDS = {
    "correction": "the model proposed X, the right answer was Y",
    "constraint": "a standing rule -- never do Z, always do W",
    "override": "an exception taken deliberately, this once",
    "rationale": "why the obvious suggestion was wrong here",
}


# How much the human actually put in. The same observation is worth different
# amounts depending on the evidence behind it, and conflating the three is how a
# store fills with notes nobody can trust later.
EVIDENCE = (
    ("disagreement", "they contradicted, corrected or overrode you -- richest, always capture"),
    ("endorsement", "they wrote approval in their own words -- capture only if it carries a reason"),
    ("acceptance", "they accepted a proposal without saying why -- never enough on its own"),
)

# How eagerly a persona captures. `bar` is spliced into the skill so the wording
# and the cap always agree; a skill that says "capture generously" while the
# allocator refuses after three notes would just look broken.
SENSITIVITY = {
    "minimal": {
        "max_per_day": 1,
        "bar": (
            "Capture almost nothing. At most one thing a day, and only if losing it "
            "would actually cost the human something. Most days, write nothing."
        ),
    },
    "low": {
        "max_per_day": 3,
        "bar": (
            "Capture sparingly. Only the insight you would still want a year from now, "
            "when most of this session is forgotten. If you are weighing it up, that "
            "hesitation is the answer: skip it."
        ),
    },
    "normal": {
        "max_per_day": 6,
        "bar": (
            "Capture what a competent colleague would not already know. A few notes a "
            "day is the shape of it -- if you are writing after every exchange, the bar "
            "has slipped."
        ),
    },
    "high": {
        "max_per_day": 12,
        "bar": (
            "Capture generously. A thin note that turns out to matter beats a lost one, "
            "and the human prunes later. Still never capture agreement, or something you "
            "already knew and merely restated."
        ),
    },
    "extreme": {
        "max_per_day": 40,
        "bar": (
            "Capture nearly every collision, including the small ones. The human has "
            "chosen volume over precision and will prune. The only bar left is the one "
            "that cannot move: never capture agreement, and never capture what you "
            "already knew and merely restated."
        ),
    },
}

# Ordered least to most eager. Dict order is not a contract worth relying on for
# something a menu and a validation message both read.
SENSITIVITY_ORDER = ("minimal", "low", "normal", "high", "extreme")

class PersonaError(ValueError):
    pass


class ScopeLevel:
    """One rung of the safety gate. Ordered least to most restrictive."""

    def __init__(self, name: str, test: str):
        self.name = name
        self.test = test


class Persona:
    def __init__(self, data: Dict[str, Any], path: str = ""):
        self.path = path
        self._data = data

        self.name = self._req("name")
        if not SLUG_RE.match(self.name):
            raise PersonaError("name must be lower-case kebab-case, got %r" % self.name)

        self.title = self._req("title")
        self.summary = self._req("summary")

        # Store. Convention by default, override honoured, never shared.
        self.store = data.get("store") or "~/.%s-notes" % self.name
        self.skill = data.get("skill") or "capture-%s-insight" % self.name

        # Domain vocabulary drives the description and the trigger list.
        domain = data.get("domain") or {}
        if not isinstance(domain, dict):
            raise PersonaError("`domain` must be a mapping")
        self.vocabulary: List[str] = self._list(domain.get("vocabulary"), "domain.vocabulary")
        self.occasions: List[str] = self._list(domain.get("occasions"), "domain.occasions")
        self.triggers: List[str] = self._list(domain.get("triggers"), "domain.triggers", required=False)
        self.capture_worthy: List[str] = self._list(domain.get("capture_worthy"), "domain.capture_worthy")
        self.skip: List[str] = self._list(domain.get("skip"), "domain.skip")

        # The durability lesson, taught by contrast. Both halves required:
        # a rule without its too-specific twin does not teach the level.
        pairs = data.get("durable_examples")
        if not pairs:
            single = data.get("durable_example") or {}
            pairs = [single] if single else []
        if not isinstance(pairs, list):
            raise PersonaError("`durable_examples` must be a list of pairs")
        self.durable_examples = []
        for pair in pairs:
            if not isinstance(pair, dict) or not pair.get("too_specific") or not pair.get("durable"):
                raise PersonaError(
                    "each durable example needs both `too_specific` and `durable` -- "
                    "the contrast is what teaches the abstraction level"
                )
            self.durable_examples.append((str(pair["too_specific"]), str(pair["durable"])))
        if not self.durable_examples:
            raise PersonaError("a persona needs at least one durable_example")
        self.durable_bad, self.durable_good = self.durable_examples[0]

        kinds = data.get("collision_kinds")
        self.collision_kinds = self._list(kinds, "collision_kinds", required=False) or list(COLLISION_KINDS)
        unknown = set(self.collision_kinds) - set(COLLISION_KINDS)
        if unknown:
            raise PersonaError("unknown collision_kinds: %s (known: %s)"
                               % (", ".join(sorted(unknown)), ", ".join(COLLISION_KINDS)))

        # Scope gate. Exactly three levels, most restrictive last.
        levels = data.get("scope") or []
        if not isinstance(levels, list) or len(levels) != 3:
            raise PersonaError("`scope` must list exactly three levels, least restrictive first")
        self.scope: List[ScopeLevel] = []
        for level in levels:
            if not isinstance(level, dict) or "name" not in level or "test" not in level:
                raise PersonaError("each scope level needs `name` and `test`")
            if not SLUG_RE.match(str(level["name"])):
                raise PersonaError("scope name must be kebab-case, got %r" % level["name"])
            self.scope.append(ScopeLevel(str(level["name"]), str(level["test"])))

        self.icon = data.get("icon") or "🧠"
        self.language_hint = data.get("language_hint", "")

        # When to prompt for a sweep. `pre_compact` is the valuable one in long
        # sessions: compaction is the moment reasoning worked out *during* the
        # session is provably about to be discarded.
        sweeps = data.get("sweep")
        if sweeps in (None, ""):
            sweeps = ["pre_compact"]
        self.sweep = self._list(sweeps, "sweep")
        unknown = set(self.sweep) - {"pre_compact", "none"}
        if unknown:
            raise PersonaError("unknown sweep trigger(s): %s" % ", ".join(sorted(unknown)))

        # How eagerly to capture. Two levers, because they fail differently:
        # `sensitivity` sets the judgement bar in the skill text, and `max_per_day`
        # is a hard stop enforced by the allocator. Judgement alone drifts over a
        # long session; a cap alone would silently swallow a genuinely good note
        # without ever raising the bar that produced the noise.
        self.sensitivity = str(data.get("sensitivity") or "normal")
        if self.sensitivity not in SENSITIVITY:
            raise PersonaError("sensitivity must be one of: %s" % ", ".join(SENSITIVITY))
        default_cap = SENSITIVITY[self.sensitivity]["max_per_day"]
        raw_cap = data.get("max_per_day", "")
        try:
            self.max_per_day = int(raw_cap) if str(raw_cap).strip() else default_cap
        except ValueError:
            raise PersonaError("max_per_day must be a whole number, got %r" % raw_cap)
        if self.max_per_day < 0:
            raise PersonaError("max_per_day cannot be negative (0 means no cap)")

    @property
    def bar(self) -> str:
        return SENSITIVITY[self.sensitivity]["bar"]

    # -- helpers ---------------------------------------------------------

    def _req(self, key: str) -> str:
        value = self._data.get(key)
        if not value or not isinstance(value, str):
            raise PersonaError("missing required field %r" % key)
        return value.strip()

    def _list(self, value, label: str, required: bool = True) -> List[str]:
        if value in (None, ""):
            if required:
                raise PersonaError("missing required list %r" % label)
            return []
        if not isinstance(value, list):
            raise PersonaError("%s must be a list" % label)
        return [str(v).strip() for v in value if str(v).strip()]

    # -- derived ---------------------------------------------------------

    @property
    def store_path(self) -> str:
        return os.path.expanduser(self.store)

    @property
    def scope_names(self) -> List[str]:
        return [s.name for s in self.scope]

    @property
    def restrictive_scope(self) -> str:
        """The rung to choose when unsure. Always the last one."""
        return self.scope[-1].name

    def description(self) -> str:
        """The always-in-context line. Built, then budgeted."""
        vocab = ", ".join(self.vocabulary)
        occasions = "; ".join(self.occasions)
        extra = ""
        if self.triggers:
            extra = " Also on %s." % ", ".join('"%s"' % t for t in self.triggers)
        return (
            "Capture a durable {vocab} insight into the private write-only store at {store}/. "
            "Use when the work yields a lasting principle: {occasions}.{extra}"
        ).format(vocab=vocab, store=self.store, occasions=occasions, extra=extra)

    def validate(self) -> List[str]:
        """Non-fatal warnings. Fatal problems raise in __init__."""
        warnings = []
        n = len(self.description())
        if n > DESCRIPTION_BUDGET_CHARS:
            warnings.append(
                "description is %d chars, over the %d budget -- trim domain.vocabulary "
                "or domain.occasions" % (n, DESCRIPTION_BUDGET_CHARS)
            )
        if len(self.capture_worthy) < 3:
            warnings.append("only %d capture_worthy examples; 4-6 teaches the bar better"
                            % len(self.capture_worthy))
        if not self.skip:
            warnings.append("no `skip` examples -- the negative cases are what stop noise")
        return warnings


def load(path: str) -> Persona:
    return Persona(miniyaml.load_file(path), path=path)
