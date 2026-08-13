"""promptie -- compile a persona definition into a capture mechanism."""

import argparse
import datetime
import subprocess
import json
import re
import os
import sys
from pathlib import Path
from typing import List

from . import install as installer
from . import miniyaml, model, render
from .install import Profile

REPO = Path(__file__).resolve().parent.parent

# Config lives with the user, not next to the code. Anything derived from the
# package location breaks the moment promptie is installed rather than run from a
# checkout -- which is the normal case.
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "promptie"
CONFIG = Path(os.environ.get("PROMPTIE_CONFIG", CONFIG_HOME / "profiles.json"))

# Personas are looked up in the user's own directory first, then the ones that
# ship with promptie. That way `promptie install mine` works without the user
# ever editing anything inside an installed package.
USER_PERSONAS = Path(os.environ.get("PROMPTIE_PERSONAS", CONFIG_HOME / "personas"))
PACKAGED_PERSONAS = REPO / "personas"
PERSONA_DIRS = [USER_PERSONAS, PACKAGED_PERSONAS]
DEFAULT_PERSONA = "collision"
PERSONA_DIR = USER_PERSONAS  # where `promptie new` would write

# Rough divisor for characters -> tokens on English prose. Good enough to keep a
# budget honest; we report characters too, which are exact.
CHARS_PER_TOKEN = 4.0


def _load_config() -> dict:
    if not CONFIG.exists():
        return {"profiles": []}
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _profiles(args) -> List[Profile]:
    if getattr(args, "profile", None):
        return [Profile(p) for p in args.profile]
    configured = _load_config().get("profiles", [])
    if not configured:
        raise SystemExit(
            "no profiles configured. Run:\n"
            "  promptie init --profile ~/.claude\n"
            "(repeat --profile for each Claude config directory you use)"
        )
    return [Profile(p) for p in configured]


def _persona_path(name: str) -> Path:
    candidate = Path(name)
    if candidate.exists():
        return candidate
    for directory in PERSONA_DIRS:
        for ext in (".yaml", ".yml"):
            guess = directory / (name + ext)
            if guess.exists():
                return guess
    raise SystemExit("no persona named %r (looked in: %s)"
                     % (name, ", ".join(str(d) for d in PERSONA_DIRS)))


def _load(name: str, overrides: dict = None) -> model.Persona:
    """Load a persona, applying any command-line overrides before validation.

    Overrides are applied to the raw mapping rather than to the built object so a
    bad --sensitivity fails the same way a bad YAML value would.
    """
    path = _persona_path(name)
    try:
        data = miniyaml.load_file(str(path))
        data.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
        return model.Persona(data, path=str(path))
    except (model.PersonaError, ValueError) as exc:
        raise SystemExit("%s: %s" % (path, exc))


def _tuning(args) -> dict:
    return {"sensitivity": getattr(args, "sensitivity", None),
            "max_per_day": getattr(args, "max_per_day", None)}


def _sensitivity_menu() -> str:
    rows = []
    for level in model.SENSITIVITY_ORDER:
        cap = model.SENSITIVITY[level]["max_per_day"]
        mark = "  <- recommended" if level == "normal" else ""
        rows.append("  %-7s up to %2d notes/day%s" % (level, cap, mark))
    return "\n".join(rows)


# -- commands -------------------------------------------------------------

def _discover_profiles():
    """Find Claude config directories without asking the user to remember them.

    A profile is a directory holding settings.json, or one Claude has clearly
    used. Guessing wrong is cheap here because the user confirms before anything
    is written.
    """
    found = []
    home = Path.home()
    for path in sorted(home.glob(".claude*")):
        if not path.is_dir():
            continue
        if (path / "settings.json").exists() or (path / "skills").exists() or path.name == ".claude":
            found.append(path)
    return found


# -- terminal presentation -------------------------------------------------
#
# Colour is applied by role, never by decoration: one accent for things the user
# must act on, one for what the machine did, one dim for detail they can ignore.
# Everything degrades to plain text when piped or when NO_COLOR is set, because a
# walkthrough that emits escape codes into a log file is worse than a plain one.

def _plain():
    """No escape codes when piped, redirected, or when NO_COLOR is set.

    A walkthrough that writes escape codes into a log file is worse than a plain
    one, and CI captures stdout far more often than people expect.
    """
    return bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


class C:
    ACCENT = "\033[38;5;39m"    # asks, headings: the user's turn
    OK = "\033[38;5;42m"        # something happened
    WARN = "\033[38;5;214m"     # a choice with consequences
    DIM = "\033[2m"
    BOLD = "\033[1m"
    OFF = "\033[0m"


def _paint(text, *codes):
    if _plain():
        return text
    return "".join(codes) + text + C.OFF


def _heading(step, total, title):
    dots = "".join("*" if i < step else "." for i in range(total))
    print()
    print(_paint("  %s  " % dots, C.ACCENT) + _paint(title, C.BOLD))
    print(_paint("  " + "-" * 62, C.DIM))


def _say(text=""):
    for line in text.splitlines() or [""]:
        print("  " + line if line else "")


def _ask(prompt, default=""):
    hint = _paint(" [%s]" % default, C.DIM) if default else ""
    try:
        answer = input("  " + _paint(prompt, C.ACCENT) + hint + " ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return answer or default


def _menu(options, default_index=0):
    """options: list of (key, label, blurb). Returns the chosen key."""
    print()
    for i, (key, label, blurb) in enumerate(options, 1):
        marker = _paint("*", C.WARN) if i - 1 == default_index else " "
        # Blurbs come from persona YAML and may be folded across lines. Flatten
        # and clip them, or a long scope test destroys the column alignment.
        flat = " ".join(str(blurb).split())
        if len(flat) > 52:
            flat = flat[:51].rsplit(" ", 1)[0] + "..."
        print("  %s %s  %-11s %s" % (marker, _paint(str(i), C.BOLD), label,
                                     _paint(flat, C.DIM)))
    picked = _ask("\n  >", str(default_index + 1))
    for i, (key, _label, _b) in enumerate(options, 1):
        if picked == str(i) or picked.lower() == key:
            return key
    return options[default_index][0]


def _confirm(prompt, default=True):
    suffix = "Y/n" if default else "y/N"
    answer = _ask(prompt, suffix).lower()
    if answer in ("y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    return default


def cmd_onboard(args):
    """Set promptie up by having the user do the thing, not by describing it.

    The demonstrations run the real installed scripts and print their real
    output, and the last step writes a real note the user authored. A walkthrough
    that fakes its output teaches the wrong thing about a tool whose whole value
    is behaving predictably in the background.
    """
    print()
    print(_paint("  promptie", C.BOLD) + _paint("  disagreement is data", C.DIM))
    print()
    _say(_paint("It captures the moments you tell Claude something it would not", C.DIM))
    _say(_paint("have produced. Those notes are yours. Claude writes them and", C.DIM))
    _say(_paint("never reads them back.", C.DIM))

    # 1. profiles ---------------------------------------------------------
    _heading(1, 4, "Where should capture be active?")
    discovered = _discover_profiles()
    if discovered:
        print()
        for i, path in enumerate(discovered, 1):
            label = str(path).replace(str(Path.home()), "~")
            print("  %s %s" % (_paint(str(i), C.BOLD), label))
        picked = _ask("\n  all, or the numbers you want >", "all").lower()
        if picked in ("all", "a", "y", "yes"):
            chosen = discovered
        else:
            try:
                chosen = [discovered[int(n) - 1] for n in picked.split()]
            except (ValueError, IndexError):
                raise SystemExit("did not understand %r" % picked)
    else:
        _say("No Claude config directory found in your home directory.")
        chosen = [Path(_ask("path to it >")).expanduser()]

    cfg = _load_config()
    cfg["profiles"] = [str(p) for p in chosen]
    _save_config(cfg)
    print()
    print(_paint("  ok", C.OK) + "  %d profile(s)" % len(chosen))

    # 2. sensitivity ------------------------------------------------------
    _heading(2, 4, "How eagerly should it capture?")
    _say(_paint("A hard cap, enforced when a note is allocated. Past the limit it", C.DIM))
    _say(_paint("refuses and says so, rather than filling the store quietly.", C.DIM))
    blurbs = {
        "minimal": "at most one, only if losing it would cost you",
        "low": "only what you would still want in a year",
        "normal": "what a colleague would not already know",
        "high": "generously, and prune later",
        "extreme": "nearly everything, volume over precision",
    }
    ordered = ["normal"] + [l for l in model.SENSITIVITY_ORDER if l != "normal"]
    level = _menu([(l, l, "%d a day. %s" % (model.SENSITIVITY[l]["max_per_day"], blurbs[l]))
                   for l in ordered])
    print()
    print(_paint("  ok", C.OK) + "  %s, %d notes a day"
          % (level, model.SENSITIVITY[level]["max_per_day"]))

    # 3. install ----------------------------------------------------------
    _heading(3, 4, "Installing")
    p = _load(DEFAULT_PERSONA, {"sensitivity": level})
    for profile in [Profile(path) for path in chosen]:
        written, _ = installer.install(p, profile)
        print("  %s %-38s %s" % (_paint("ok", C.OK),
                                 str(profile.path).replace(str(Path.home()), "~"),
                                 _paint("%d files" % len(written), C.DIM)))
    print()
    _say(_paint("store: %s" % p.store.replace(str(Path.home()), "~"), C.DIM))

    sdir = installer.skill_dir(p, Profile(chosen[0]))
    hooks = Path(chosen[0]) / "hooks" / ("%s_capture_hooks.py" % p.name.replace("-", "_"))
    exe = installer.runtime_python()

    # 4. capture something real -------------------------------------------
    _heading(4, 4, "Capture your first note")
    _say("Think of something you had to correct Claude about. A constraint it")
    _say("did not know, a suggestion that was wrong for a reason only you knew.")
    print()
    if not _confirm("Write it down now?"):
        _say(_paint("\nFine. It will happen on its own as you work.", C.DIM))
        return _closing(p)

    print()
    rule = _ask("the rule, as an instruction >")
    while not rule:
        rule = _ask("the rule, as an instruction >")
    why = _ask("what breaks without it >")

    _say(_paint("\nWhat kind of collision was it?", C.DIM))
    kind = _menu([(k, k, model.COLLISION_KINDS[k]) for k in p.collision_kinds])

    _say(_paint("\nHow far does it travel?", C.DIM))
    scope = _menu([(s.name, s.name, s.test) for s in p.scope])

    # Hyphens in the prose are word joins, not noise: "press-fits" must not
    # become "pressfits" in the filename the user will read later.
    words = re.sub(r"[^a-z0-9]+", " ", rule.lower()).split()
    slug = "-".join(words[:5]) or "first-note"
    allocated = subprocess.run([exe, str(sdir / "new_note.py"), slug],
                               capture_output=True, text=True)
    if allocated.returncode != 0:
        raise SystemExit(allocated.stderr.strip())
    path = Path(allocated.stdout.strip())
    number = path.name[:6]
    today = datetime.date.today().isoformat()
    path.write_text(
        "---\nname: %s\ndescription: %s\nkind: %s\nevidence: disagreement\n"
        "scope: %s\ncaptured: %s\nsource: onboarding\n---\n\n%s\n\n"
        "**Why:** %s\n\n**Origin:** Written during promptie onboarding.\n"
        % (slug, rule, kind, scope, today, rule, why or "(not stated)"),
        encoding="utf-8")
    subprocess.run([exe, str(sdir / "append_index.py"), number, today, slug,
                    scope, kind, rule[:60]], capture_output=True, text=True)

    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(path)}})
    shown = subprocess.run([exe, str(hooks), "notify"], input=payload,
                           capture_output=True, text=True)
    print()
    _say(_paint("That is exactly what you will see in Claude when it captures:", C.DIM))
    print()
    try:
        print("  " + _paint(json.loads(shown.stdout)["systemMessage"], C.OK))
    except Exception:
        print("  (hook produced no output)")
    print()
    _say(_paint(str(path).replace(str(Path.home()), "~"), C.DIM))
    return _closing(p, first=True)


def _closing(p, first=False):
    print()
    print(_paint("  " + "-" * 62, C.DIM))
    if first:
        _say("That note was written by the same scripts Claude uses. Nothing")
        _say("about it is a demonstration.")
        print()
    _say("From here you do nothing. Prompt normally.")
    print()
    print("  %-22s %s" % (_paint("promptie list", C.BOLD),
                          _paint("your notes, oldest first", C.DIM)))
    print("  %-22s %s" % (_paint("promptie stats", C.BOLD),
                          _paint("where your corrections cluster", C.DIM)))
    print("  %-22s %s" % (_paint("promptie sensitivity", C.BOLD),
                          _paint("change the rate later", C.DIM)))
    print()
    print("  %-22s %s" % (_paint("PROMPTIE_DISABLE=1", C.WARN),
                          _paint("off for this shell", C.DIM)))
    print("  %-22s %s" % (_paint(".promptieignore", C.WARN),
                          _paint("off for this directory tree", C.DIM)))
    print()
    _say(_paint("Start a new Claude session for the hooks to load.", C.ACCENT))
    print()
    return 0


def cmd_init(args):
    cfg = _load_config()
    profiles = cfg.get("profiles", [])
    for raw in args.profile:
        path = str(Path(raw).expanduser())
        if not Path(path).exists():
            print("warning: %s does not exist yet; it will be created on install" % path)
        if path not in profiles:
            profiles.append(path)
    cfg["profiles"] = profiles
    _save_config(cfg)
    print("configured profiles (%s):" % CONFIG)
    for p in profiles:
        print("  " + p)
    return 0


def cmd_profiles(args):
    profiles = _load_config().get("profiles", [])
    print("\n".join(profiles) if profiles else "(none configured -- run: promptie init --profile ...)")
    return 0


def cmd_personas(args):
    seen, found = set(), []
    for directory in PERSONA_DIRS:
        for path in sorted(directory.glob("*.y*ml")) if directory.exists() else []:
            if path.stem not in seen:
                seen.add(path.stem)
                found.append(path)
    if not found:
        print("no personas found in: %s" % ", ".join(str(d) for d in PERSONA_DIRS))
        return 0
    for path in found:
        try:
            p = model.load(str(path))
            print("%-12s %-28s -> %s" % (p.name, p.title, p.store))
        except Exception as exc:
            print("%-12s (invalid: %s)" % (path.stem, exc))
    return 0


def cmd_check(args):
    p = _load(args.persona)
    desc = p.description()
    chars = len(desc)
    print("persona:      %s (%s)" % (p.name, p.title))
    print("skill:        %s" % p.skill)
    print("store:        %s" % p.store)
    print("scope levels: %s   (unsure -> %s)" % (", ".join(p.scope_names), p.restrictive_scope))
    print()
    print("per-turn context cost")
    print("  description: %d chars  (~%d tokens, budget %d chars)"
          % (chars, round(chars / CHARS_PER_TOKEN), model.DESCRIPTION_BUDGET_CHARS))
    arming = render.arming_line(p)
    print("  SessionStart injection: %d chars (~%d tokens, once per session)"
          % (len(arming), round(len(arming) / CHARS_PER_TOKEN)))
    skill = render.skill_md(p)
    print("  SKILL.md body: %d chars (~%d tokens, loaded only when the skill fires)"
          % (len(skill), round(len(skill) / CHARS_PER_TOKEN)))
    print()
    print("description:")
    print("  " + desc)

    warnings = p.validate()
    if warnings:
        print()
        for w in warnings:
            print("warning: %s" % w)
        return 1 if any("over the" in w for w in warnings) else 0
    print("\nok")
    return 0


def cmd_preview(args):
    p = _load(args.persona)
    parts = {
        "SKILL.md": render.skill_md,
        "new_note.py": render.new_note_py,
        "append_index.py": render.append_index_py,
        "hooks.py": render.hooks_py,
        "store README.md": render.store_readme,
    }
    if args.file:
        matches = [k for k in parts if args.file in k]
        if not matches:
            raise SystemExit("no such artefact: %s (have: %s)" % (args.file, ", ".join(parts)))
        print(parts[matches[0]](p))
        return 0
    for name, fn in parts.items():
        print("=" * 70)
        print("== " + name)
        print("=" * 70)
        print(fn(p))
        print()
    return 0


def cmd_install(args):
    p = _load(args.persona, _tuning(args))
    for w in p.validate():
        print("warning: %s" % w)
    print("sensitivity: %s (cap %d notes/day)" % (p.sensitivity, p.max_per_day))
    print(_sensitivity_menu())
    print("change it any time:  promptie sensitivity %s high" % p.name)
    for profile in _profiles(args):
        written, perms = installer.install(p, profile,
                                           scaffold_store=not args.no_store)
        print("\n%s" % profile.path)
        for path in written:
            print("  wrote %s" % path)
        print("  settings.json: %d permission entries, SessionStart + PostToolUse hooks"
              % len(perms))
    print("\nstore: %s" % p.store_path)
    return 0


def cmd_config(args):
    """Read or set a setting, in the shape of `git config`.

    With no value it prints. With one it writes and reinstalls, because the
    setting lives in two generated places at once -- the wording in SKILL.md and
    the hard cap in the allocator -- and they must never disagree.
    """
    p = _load(DEFAULT_PERSONA)
    if not args.key:
        print()
        print("  %-14s %s" % ("store", p.store))
        print("  %-14s %s  %s" % ("sensitivity", p.sensitivity,
                                  _paint("(%d notes/day)" % p.max_per_day, C.DIM)))
        print("  %-14s %s" % ("icon", p.icon))
        print("  %-14s %s" % ("skill", p.skill))
        print()
        print(_sensitivity_menu())
        print()
        print(_paint("  promptie config sensitivity <level>", C.DIM))
        return 0

    if args.key != "sensitivity":
        raise SystemExit("only `sensitivity` is settable here. Edit the persona in "
                         "%s for anything else." % USER_PERSONAS)
    args.level = args.value
    args.max_per_day = None
    args.profile = None
    return cmd_sensitivity(args)


def cmd_sensitivity(args):
    """Retune an installed persona without editing YAML.

    Reinstalls with the new value, because the setting lives in two generated
    places at once -- the wording in SKILL.md and the hard cap in the allocator --
    and they must never disagree.
    """
    level = args.level
    if level and level not in model.SENSITIVITY:
        raise SystemExit("sensitivity must be one of: %s\n%s"
                         % (", ".join(model.SENSITIVITY), _sensitivity_menu()))
    p = _load(args.persona, {"sensitivity": level, "max_per_day": args.max_per_day})
    print("%s -> sensitivity %s, cap %d notes/day" % (p.name, p.sensitivity, p.max_per_day))
    for profile in _profiles(args):
        installer.install(p, profile, scaffold_store=False)
        print("  updated %s" % profile.path)
    print("\n%s" % _sensitivity_menu())
    return 0


def cmd_export(args):
    """Derive a machine-readable view from the notes.

    The store stays one human-readable format; anything structured is derived on
    demand rather than written alongside, because two written formats drift and
    the markdown is the one a human will still be able to read in ten years.

    This reads the store -- which is the point. Write-only constrains the
    assistant, not the human's own tooling.
    """
    store = Path(_load(args.persona).store_path)
    if not store.exists():
        raise SystemExit("no store at %s" % store)

    notes = []
    for path in sorted(store.glob("[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]*-*.md")):
        text = path.read_text(encoding="utf-8")
        meta, _, body = text.partition("---\n")[2].partition("\n---\n")
        record = {"number": path.name.split("-", 1)[0], "file": path.name}
        for line in meta.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                record[key.strip()] = value.strip()
        record["body"] = body.strip()
        record["bytes"] = path.stat().st_size
        notes.append(record)

    if args.format == "jsonl":
        out = "\n".join(json.dumps(n, ensure_ascii=False) for n in notes)
    else:
        out = json.dumps(notes, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print("wrote %d notes to %s" % (len(notes), args.out))
    else:
        print(out)
    return 0


def cmd_cost(args):
    """What this actually consumes, in tokens and in bytes.

    Both numbers matter and they point opposite ways: the disk cost is negligible
    and the per-turn token cost is not, so the thing worth watching is the
    description, not the store.
    """
    p = _load(args.persona)
    desc = len(p.description())
    arm = len(render.arming_line(p))
    body = len(render.skill_md(p))
    turns, sessions = args.turns, args.sessions

    per_turn = desc / CHARS_PER_TOKEN
    per_session = arm / CHARS_PER_TOKEN
    fires = p.max_per_day
    daily = per_turn * turns * sessions + per_session * sessions + (body / CHARS_PER_TOKEN) * fires

    print("%s, assuming %d sessions/day of %d turns each" % (p.name, sessions, turns))
    print("  description   %6.0f tok/day  (%d chars x %d turns)"
          % (per_turn * turns * sessions, desc, turns * sessions))
    print("  session arm   %6.0f tok/day" % (per_session * sessions))
    print("  skill body    %6.0f tok/day  (worst case: cap of %d captures all fire)"
          % ((body / CHARS_PER_TOKEN) * fires, fires))
    print("  ------------------------")
    print("  total         %6.0f tok/day   ~%.1fM tok/month" % (daily, daily * 30 / 1e6))

    store = Path(p.store_path)
    notes = list(store.glob("[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]*-*.md")) if store.exists() else []
    size = sum(f.stat().st_size for f in notes)
    avg = (size / len(notes)) if notes else 700
    print()
    print("disk: %d notes, %s" % (len(notes), _human(size)))
    print("  at the cap (%d/day) that is %s/year -- storage is not the constraint"
          % (p.max_per_day, _human(avg * p.max_per_day * 365)))
    return 0


def _human(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


INDEX_LINE = re.compile(
    r"^- `(?P<num>[0-9A-Z]{5,9})` - `(?P<date>[\d-]{10})` - \[(?P<slug>[^\]]+)\]\([^)]*\) - "
    r"`(?P<kind>[a-z]+)` - `(?P<scope>[a-z]+)` - (?P<hook>.*)$")


def _index_rows(store: Path):
    """Parse INDEX.md lines only. Never opens a note.

    The index is metadata the human already agreed to write; reading it gives
    counts and distributions without any note content being surfaced.
    """
    index = store / "INDEX.md"
    if not index.exists():
        return []
    rows = []
    for line in index.read_text(encoding="utf-8").splitlines():
        match = INDEX_LINE.match(line.strip())
        if match:
            rows.append(match.groupdict())
    return rows


def cmd_list(args):
    """Every note, oldest first, so the newest is where your eye already is.

    Reads the index lines only, never a note. That keeps the one surface you
    browse cheap, and it means this command shows exactly what the store itself
    promised to record.
    """
    p = _load(args.persona)
    rows = _index_rows(Path(p.store_path))
    if not rows:
        print("no notes yet in %s" % p.store)
        return 0

    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    if args.scope:
        rows = [r for r in rows if r["scope"] == args.scope]

    width = max(len(r["hook"]) for r in rows)
    for row in rows:
        print("  %s  %s  %s  %s" % (
            _paint(row["num"], C.DIM),
            row["date"],
            row["hook"].ljust(width),
            _paint("%s %s" % (row["kind"], row["scope"]), C.DIM)))
    print()
    print("  %d note%s in %s" % (len(rows), "" if len(rows) == 1 else "s", p.store))
    return 0


def _find_note(store: Path, ident: str):
    """Locate a note by id or by a fragment of its slug."""
    matches = sorted(store.glob("%s-*.md" % ident))
    if not matches:
        matches = sorted(p for p in store.glob("*-*.md") if ident.lower() in p.name.lower())
    if not matches:
        raise SystemExit("no note matching %r in %s" % (ident, store))
    if len(matches) > 1:
        print("several notes match %r:" % ident)
        for path in matches:
            print("  %s" % path.name)
        raise SystemExit(1)
    return matches[0]


def cmd_read(args):
    """Print one note.

    The write-only rule binds the assistant, not you. Without a way to read a
    note from here, `list` is a dead end: you see a line worth following and have
    to go find the file yourself.
    """
    p = _load(args.persona)
    path = _find_note(Path(p.store_path), args.note)
    print()
    print(_paint(path.name, C.DIM))
    print()
    print(path.read_text(encoding="utf-8").rstrip())
    print()
    return 0


def cmd_search(args):
    """Full-text search across the notes.

    Once a store passes a hundred notes, listing them is no longer finding them.
    This is the difference between an archive and a pile.
    """
    p = _load(args.persona)
    store = Path(p.store_path)
    needle = args.query.lower()
    hits = 0
    for path in sorted(store.glob("*-*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        found = [(n, l) for n, l in enumerate(lines, 1) if needle in l.lower()]
        if not found:
            continue
        hits += 1
        print()
        print(_paint(path.name, C.BOLD))
        for n, line in found[:args.context]:
            print("  %s  %s" % (_paint("%4d" % n, C.DIM), line.strip()))
    print()
    print("  %d note%s matched %r" % (hits, "" if hits == 1 else "s", args.query))
    return 0


def cmd_rm(args):
    """Delete a note, and rebuild the index without it.

    Deduplication and pruning are the human's job by design, and until now there
    was no tool for the job. Deleting the file alone would leave a dangling index
    line, so the index is rebuilt from what remains.
    """
    p = _load(args.persona)
    store = Path(p.store_path)
    path = _find_note(store, args.note)
    print(path.read_text(encoding="utf-8").rstrip()[:400])
    if not args.yes:
        answer = _ask("\n  delete this note? [y/N]", "n").lower()
        if answer not in ("y", "yes"):
            print("  kept")
            return 0
    path.unlink()

    index = store / "INDEX.md"
    if index.exists():
        kept = [l for l in index.read_text(encoding="utf-8").splitlines()
                if path.name not in l]
        index.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print("  deleted %s" % path.name)
    return 0


def cmd_open(args):
    """Open the store in whatever the platform uses for a directory."""
    p = _load(args.persona)
    store = Path(p.store_path)
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
    subprocess.call([opener, str(store)])
    print("  %s" % store)
    return 0


def cmd_doctor(args):
    """Check that capture is actually live, and say so plainly.

    The worst failure this tool can have is looking installed while doing
    nothing: hooks load at session start, so a session that began before the
    install has none, and silence looks identical either way. This command is the
    answer to "why is nothing being captured".
    """
    p = _load(args.persona)
    problems = []

    def check(label, ok, detail=""):
        mark = _paint("ok  ", C.OK) if ok else _paint("FAIL", C.WARN)
        print("  %s %-34s %s" % (mark, label, _paint(detail, C.DIM)))
        if not ok:
            problems.append(label)

    print()
    profiles = _load_config().get("profiles", [])
    check("profiles configured", bool(profiles), ", ".join(profiles) or "run: promptie onboard")

    for raw in profiles:
        profile = Profile(raw)
        sdir = installer.skill_dir(p, profile)
        check("skill installed", (sdir / "SKILL.md").exists(), str(sdir))
        check("scripts installed",
              (sdir / "new_note.py").exists() and (sdir / "append_index.py").exists())

        settings = profile.load_settings()
        hooks = json.dumps(settings.get("hooks", {}))
        for event in ("SessionStart", "PostToolUse", "PreCompact"):
            check("%s hook registered" % event, event in settings.get("hooks", {}) and
                  p.name.replace("-", "_") in hooks)

        allow = settings.get("permissions", {}).get("allow", [])
        writes = [e for e in allow if e.startswith(("Write(", "Edit("))
                  and p.store_path in e]
        check("store pre-authorised", len(writes) == 2,
              "without this every capture asks permission")
        check("permission paths absolute", all(e.split("(")[1].startswith("//") for e in writes))

    store = Path(p.store_path)
    check("store exists", store.exists(), str(store))
    if store.exists():
        probe = store / ".promptie-write-probe"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            check("store writable", True)
        except OSError as exc:
            check("store writable", False, str(exc))

    exe = installer.runtime_python()
    check("interpreter still present", os.path.exists(exe), exe)

    print()
    if problems:
        print("  %s  run: promptie install" % _paint("%d problem(s)" % len(problems), C.WARN))
        return 1
    print("  %s" % _paint("capture is live", C.OK))
    print(_paint("  Hooks load when a session starts, so start a new session after "
                 "installing.", C.DIM))
    return 0


def cmd_stats(args):
    p = _load(args.persona)
    store = Path(p.store_path)
    rows = _index_rows(store)
    files = list(store.glob("[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]*-*.md")) if store.exists() else []
    print("%s -- %d notes in %s" % (p.name, len(files), p.store))
    if not rows:
        print("(no index rows yet)")
        return 0

    def tally(field):
        counts = {}
        for r in rows:
            counts[r[field]] = counts.get(r[field], 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    for field in ("kind", "scope"):
        print("\nby %s:" % field)
        for value, n in tally(field):
            print("  %-12s %3d  %s" % (value, n, "#" * min(n, 40)))

    weeks = {}
    for r in rows:
        year, week, _ = datetime.date(*map(int, r["date"].split("-"))).isocalendar()
        weeks["%d-w%02d" % (year, week)] = weeks.get("%d-w%02d" % (year, week), 0) + 1
    print("\nby week:")
    for label in sorted(weeks)[-8:]:
        print("  %-10s %3d  %s" % (label, weeks[label], "#" * min(weeks[label], 40)))
    return 0


def cmd_uninstall(args):
    p = _load(args.persona)
    for profile in _profiles(args):
        print("\n%s" % profile.path)
        for item in installer.uninstall(p, profile):
            print("  removed %s" % item)
    print("\nstore left untouched at %s" % p.store_path)
    return 0


# -- wiring ---------------------------------------------------------------

class _Help(argparse.RawDescriptionHelpFormatter):
    """argparse wraps a command onto a second line once its name passes 12
    characters, which splits 'sensitivity' and 'candidates' from their
    descriptions. Widen the column instead."""

    def __init__(self, prog):
        super(_Help, self).__init__(prog, max_help_position=32, width=88)

    def add_arguments(self, actions):
        # argparse sizes the description column from the widest *top-level*
        # invocation, which here is "-h, --help". Subcommand names are longer
        # than that, so they get wrapped. Give the column a floor.
        super(_Help, self).add_arguments(actions)
        self._action_max_length = max(self._action_max_length, 18)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="promptie",
        description="Keep what you know. promptie records the moments you correct Claude.",
        # metavar suppresses argparse's brace list of every subcommand, which is
        # noise in both the usage line and the body. The named list below it is
        # the useful form.
        formatter_class=_Help,
        epilog="Start with:  promptie onboard")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>", title="commands")

    onb = sub.add_parser("onboard", help="guided first-time setup (start here)")
    onb.set_defaults(fn=cmd_onboard)

    doc = sub.add_parser("status", aliases=["doctor"],
                         help="check that capture is actually live")
    doc.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    doc.set_defaults(fn=cmd_doctor)

    lst = sub.add_parser("log", aliases=["list", "ls"],
                         help="your notes, oldest first")
    lst.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    lst.add_argument("-k", "--kind", choices=sorted(model.COLLISION_KINDS))
    lst.add_argument("-s", "--scope")
    lst.set_defaults(fn=cmd_list)

    rd = sub.add_parser("show", aliases=["read", "cat"], help="print one note")
    rd.add_argument("note", help="id, or part of the slug")
    rd.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    rd.set_defaults(fn=cmd_read)

    se = sub.add_parser("grep", aliases=["search"],
                        help="search across your notes")
    se.add_argument("query")
    se.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    se.add_argument("-c", "--context", type=int, default=3, help="lines shown per note")
    se.set_defaults(fn=cmd_search)

    rm = sub.add_parser("rm", help="delete a note and rebuild the index")
    rm.add_argument("note", help="id, or part of the slug")
    rm.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    rm.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    rm.set_defaults(fn=cmd_rm)

    cfg = sub.add_parser("config", help="show or set settings")
    cfg.add_argument("key", nargs="?", choices=["sensitivity"])
    cfg.add_argument("value", nargs="?")
    cfg.set_defaults(fn=cmd_config)

    sen = sub.add_parser("sensitivity", help="shorthand for config sensitivity")
    sen.add_argument("level", nargs="?", choices=model.SENSITIVITY_ORDER,
                     help="low, normal or high")
    sen.add_argument("--persona", default=DEFAULT_PERSONA)
    sen.add_argument("--max-per-day", dest="max_per_day", type=int)
    sen.add_argument("--profile", action="append")
    sen.set_defaults(fn=cmd_sensitivity)

    st = sub.add_parser("stats", help="counts and distributions from INDEX.md only")
    st.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    st.set_defaults(fn=cmd_stats)


    cost = sub.add_parser("cost", help="estimate token and disk cost")
    cost.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    cost.add_argument("--turns", type=int, default=40, help="turns per session (default 40)")
    cost.add_argument("--sessions", type=int, default=4, help="sessions per day (default 4)")
    cost.set_defaults(fn=cmd_cost)

    exp = sub.add_parser("export", help="derive a machine-readable view of the store")
    exp.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    exp.add_argument("--format", choices=["json", "jsonl"], default="json")
    exp.add_argument("-o", "--out")
    exp.set_defaults(fn=cmd_export)

    op = sub.add_parser("open", help="open the store in a file manager")
    op.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    op.set_defaults(fn=cmd_open)

    ins = sub.add_parser("install", help="install a persona into the configured profiles")
    ins.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    ins.add_argument("--profile", action="append", help="override configured profiles")
    ins.add_argument("--no-store", action="store_true", help="skip store scaffolding")
    ins.add_argument("--sensitivity", choices=model.SENSITIVITY_ORDER,
                     help="how eagerly to capture (default: the persona's own setting)")
    ins.add_argument("--max-per-day", dest="max_per_day", type=int,
                     help="hard cap on notes per day; 0 disables the cap")
    ins.set_defaults(fn=cmd_install)

    # The level comes first because "promptie sensitivity high" is what anyone
    # would type. The persona is the rare part, so it moves to a flag.
    un = sub.add_parser("uninstall", help="remove a persona's mechanism (keeps the store)")
    un.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    un.add_argument("--profile", action="append")
    un.set_defaults(fn=cmd_uninstall)
    init = sub.add_parser("init", help="configure which Claude profiles receive personas")
    init.add_argument("--profile", action="append", required=True,
                      help="path to a Claude config dir (repeatable)")
    init.set_defaults(fn=cmd_init)

    sub.add_parser("profiles", help="show configured profiles").set_defaults(fn=cmd_profiles)
    sub.add_parser("personas", help="show available persona definitions").set_defaults(fn=cmd_personas)

    chk = sub.add_parser("check", help="validate a persona and measure its context cost")
    chk.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    chk.set_defaults(fn=cmd_check)

    pv = sub.add_parser("preview", help="print generated artefacts without installing")
    pv.add_argument("persona", nargs="?", default=DEFAULT_PERSONA)
    pv.add_argument("-f", "--file", help="only this artefact, e.g. SKILL.md")
    pv.set_defaults(fn=cmd_preview)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        installed = _load_config().get("profiles")
        print()
        print("  %s  %s" % (_paint("promptie", C.BOLD),
                            _paint("disagreement is data", C.DIM)))
        print()
        if not installed:
            print("  Not set up yet. This walks you through it:")
            print("    %s" % _paint("promptie onboard", C.ACCENT))
        else:
            print("  %-20s %s" % (_paint("promptie list", C.BOLD),
                                  _paint("your notes, oldest first", C.DIM)))
            print("  %-20s %s" % (_paint("promptie stats", C.BOLD),
                                  _paint("where your corrections cluster", C.DIM)))
            print("  %-20s %s" % (_paint("promptie sensitivity", C.BOLD),
                                  _paint("capture more or less often", C.DIM)))
        print()
        print("  %s for everything else" % _paint("promptie --help", C.DIM))
        print()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
