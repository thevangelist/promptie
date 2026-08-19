"""Installing a compiled persona into a Claude profile.

Idempotent by construction: every generated file is overwritten wholesale, and
every settings.json entry is keyed by its script path so re-installing replaces
rather than duplicates. Uninstalling removes exactly what that path matched.

The store is scaffolded but never rewritten -- notes already in it are the user's,
and README.md / INDEX.md are left alone once they exist.

The generated runtime is pure standard library: no jq, no bash, no shell
utilities, so it runs unchanged on macOS, Linux, BSD and Windows.
"""

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from . import render
from .model import Persona


def runtime_python() -> str:
    """The interpreter baked into the generated hooks and permission entries.

    Deliberately not `sys.executable`: promptie may itself be running inside a
    pipx or venv interpreter, and baking that in would make capture stop working
    the day promptie is uninstalled or its venv is rebuilt. The generated scripts
    are pure standard library, so any reasonably modern python runs them -- what
    matters is picking one that will still be there tomorrow.

    An absolute path is what makes Windows work: no PATH lookup at hook time, and
    no python/python3 ambiguity.
    """
    for candidate in ("python3", "python"):
        found = shutil.which(candidate)
        if found:
            return os.path.realpath(found)
    return sys.executable


class Profile:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    @property
    def settings(self) -> Path:
        return self.path / "settings.json"

    @property
    def skills(self) -> Path:
        return self.path / "skills"

    @property
    def hooks(self) -> Path:
        return self.path / "hooks"

    def load_settings(self) -> Dict:
        if not self.settings.exists():
            return {}
        try:
            return json.loads(self.settings.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit("%s is not valid JSON (%s); fix it before installing."
                             % (self.settings, exc))

    def save_settings(self, data: Dict) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")


def _write(path: Path, content: str, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _quote(path) -> str:
    return '"%s"' % path


def skill_dir(p: Persona, profile: Profile) -> Path:
    return profile.skills / p.skill


def _hook_command(script: Path, mode: str = "") -> str:
    """The command settings.json runs.

    The interpreter is an absolute path, which is what makes Windows work: no
    PATH lookup at hook time, and no python/python3 ambiguity.
    """
    return "%s %s %s" % (_quote(runtime_python()), _quote(script), mode)


def _permission_entries(p: Persona, sdir: Path) -> List[str]:
    """Pre-authorise exactly the capture path, so a note never asks permission.

    File rules take an absolute path as `//abs/path`. A single leading slash is
    read as relative to the project root, which silently fails to match a store
    that lives in the home directory, and every capture then prompts.

    Only an Edit rule is granted. Claude Code matches file permissions against
    Edit rules alone, and an Edit rule covers every file-editing tool including
    Write -- so a Write rule never matches anything and the client warns about
    it on every startup. Granting both looked safer and was only noise.
    """
    store = "//%s/**" % p.store_path.lstrip("/")
    file_rules = ["Edit(%s)" % store]
    exe = runtime_python()
    return [
        "Bash(%s %s/new_note.py *)" % (exe, sdir),
        "Bash(%s %s/append_index.py *)" % (exe, sdir),
    ] + file_rules


def _strip(entries: List, needle: str) -> List:
    return [e for e in entries if needle not in json.dumps(e)]


def install(p: Persona, profile: Profile, scaffold_store: bool = True) -> Tuple[List[Path], List[str]]:
    sdir = skill_dir(p, profile)
    written = [_write(sdir / "SKILL.md", render.skill_md(p, str(sdir)))]

    hooks_file = profile.hooks / ("%s_capture_hooks.py" % p.name.replace("-", "_"))
    written += [
        _write(sdir / "new_note.py", render.new_note_py(p), executable=True),
        _write(sdir / "append_index.py", render.append_index_py(p), executable=True),
        _write(hooks_file, render.hooks_py(p), executable=True),
    ]
    hook_specs = [
        ("SessionStart", None, _hook_command(hooks_file, "arm")),
        ("PostToolUse", "Write|Edit", _hook_command(hooks_file, "notify")),
    ]
    if "pre_compact" in p.sweep:
        hook_specs.append(("PreCompact", None, _hook_command(hooks_file, "sweep")))

    if scaffold_store:
        store = Path(p.store_path)
        store.mkdir(parents=True, exist_ok=True)
        for name, body in (("README.md", render.store_readme(p)),
                           ("INDEX.md", render.store_index(p))):
            target = store / name
            if not target.exists():
                written.append(_write(target, body))

    settings = profile.load_settings()

    perms = settings.setdefault("permissions", {}).setdefault("allow", [])
    # Drop this persona's earlier entries before adding the current ones.
    perms[:] = [e for e in perms if str(sdir) not in e and p.store_path not in e]
    for entry in _permission_entries(p, sdir):
        if entry not in perms:
            perms.append(entry)

    hooks = settings.setdefault("hooks", {})
    # Strip every marker this persona could have left behind, including the
    # names an older version used, so an upgrade never leaves two hook sets
    # registered for one persona. Both would fire.
    markers = [str(hooks_file), str(sdir),
               "%s-capture-" % p.name,
               "%s_capture_hooks.py" % p.name.replace("-", "_")]
    for event in ("SessionStart", "PostToolUse", "PreCompact"):
        if event in hooks:
            for marker in markers:
                hooks[event] = _strip(hooks[event], marker)
    for event, matcher, command in hook_specs:
        entry = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
    for event in list(hooks):
        if not hooks[event]:
            del hooks[event]

    profile.save_settings(settings)
    return written, _permission_entries(p, sdir)


def uninstall(p: Persona, profile: Profile) -> List[str]:
    """Removes the mechanism. Never removes the store."""
    removed = []
    sdir = skill_dir(p, profile)
    if sdir.exists():
        for path in sorted(sdir.iterdir()):
            path.unlink()
            removed.append(str(path))
        sdir.rmdir()

    stem_py = "%s_capture_hooks.py" % p.name.replace("-", "_")
    for path in sorted(profile.hooks.glob("%s-capture-*" % p.name)) if profile.hooks.exists() else []:
        path.unlink()
        removed.append(str(path))
    py_hooks = profile.hooks / stem_py
    if py_hooks.exists():
        py_hooks.unlink()
        removed.append(str(py_hooks))

    settings = profile.load_settings()
    perms = settings.get("permissions", {}).get("allow", [])
    if "permissions" in settings:
        kept = [e for e in perms if str(sdir) not in e and p.store_path not in e]
        # Remove the container too when it empties. Uninstalling should leave the
        # file as it would have been had promptie never touched it.
        if kept:
            settings["permissions"]["allow"] = kept
        else:
            settings["permissions"].pop("allow", None)
            if not settings["permissions"]:
                settings.pop("permissions")

    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = _strip(hooks[event], str(sdir))
        hooks[event] = _strip(hooks[event], "%s-capture-" % p.name)
        hooks[event] = _strip(hooks[event], stem_py)
        if not hooks[event]:
            del hooks[event]
    if "hooks" in settings and not settings["hooks"]:
        settings.pop("hooks")

    profile.save_settings(settings)
    removed.append("settings.json entries in %s" % profile.settings)
    return removed
