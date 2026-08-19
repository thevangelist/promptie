"""Tests for promptie. Standard library only, run with:

    python3 -m unittest discover -s tests -v

The end-to-end tests install into a temporary HOME and then run the generated
scripts as subprocesses, which is the only way to test what actually ships: the
generator's output is the product, not the generator.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promptie import install as installer  # noqa: E402
from promptie import cli, miniyaml, model, render  # noqa: E402
from promptie.install import Profile  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COLLISION = REPO / "personas" / "collision.yaml"


# -- unit: the YAML subset ------------------------------------------------

class TestMiniYaml(unittest.TestCase):

    def test_scalars_and_quotes(self):
        data = miniyaml.loads('a: one\nb: "two"\nc: 3\n')
        self.assertEqual(data, {"a": "one", "b": "two", "c": "3"})

    def test_comments_are_stripped_but_not_inside_quotes(self):
        data = miniyaml.loads('a: one # trailing\nb: "has # hash"\n')
        self.assertEqual(data["a"], "one")
        self.assertEqual(data["b"], "has # hash")

    def test_list(self):
        data = miniyaml.loads("items:\n  - one\n  - two\n")
        self.assertEqual(data["items"], ["one", "two"])

    def test_folded_block_joins_lines(self):
        data = miniyaml.loads("text: >\n  one\n  two\nafter: x\n")
        self.assertEqual(data["text"], "one two")
        self.assertEqual(data["after"], "x")

    def test_literal_block_keeps_newlines(self):
        data = miniyaml.loads("text: |\n  one\n  two\nafter: x\n")
        self.assertEqual(data["text"], "one\ntwo")
        self.assertEqual(data["after"], "x")

    def test_nested_mapping(self):
        data = miniyaml.loads("outer:\n  inner: value\n  other: two\n")
        self.assertEqual(data["outer"], {"inner": "value", "other": "two"})

    def test_list_of_mappings(self):
        data = miniyaml.loads("scope:\n  - name: a\n    test: t1\n  - name: b\n    test: t2\n")
        self.assertEqual(data["scope"], [{"name": "a", "test": "t1"},
                                         {"name": "b", "test": "t2"}])

    def test_top_level_must_be_a_mapping(self):
        with self.assertRaises(miniyaml.YamlError):
            miniyaml.loads("- just\n- a list\n")


# -- unit: persona validation ---------------------------------------------

def _minimal(**overrides):
    data = {
        "name": "test",
        "title": "Test domain",
        "summary": "A summary.",
        "domain": {
            "vocabulary": ["testing"],
            "occasions": ["something happens"],
            "capture_worthy": ["a rule", "another rule", "a third"],
            "skip": ["noise"],
        },
        "durable_example": {"too_specific": "bad", "durable": "good"},
        "scope": [{"name": "universal", "test": "t1"},
                  {"name": "contextual", "test": "t2"},
                  {"name": "private", "test": "t3"}],
    }
    data.update(overrides)
    return data


class TestPersona(unittest.TestCase):

    def test_minimal_persona_is_valid(self):
        p = model.Persona(_minimal())
        self.assertEqual(p.store, "~/.test-notes")
        self.assertEqual(p.restrictive_scope, "private")
        self.assertEqual(p.sensitivity, "normal")
        self.assertEqual(p.max_per_day, 6)

    def test_name_must_be_kebab_case(self):
        with self.assertRaises(model.PersonaError):
            model.Persona(_minimal(name="Not Kebab"))

    def test_scope_needs_exactly_three_rungs(self):
        two = [{"name": "a", "test": "t"}, {"name": "b", "test": "t"}]
        with self.assertRaises(model.PersonaError):
            model.Persona(_minimal(scope=two))

    def test_durable_example_needs_both_halves(self):
        with self.assertRaises(model.PersonaError):
            model.Persona(_minimal(durable_example={"durable": "only one"}))

    def test_several_durable_examples(self):
        p = model.Persona(_minimal(durable_examples=[
            {"too_specific": "a", "durable": "b"},
            {"too_specific": "c", "durable": "d"}]))
        self.assertEqual(len(p.durable_examples), 2)

    def test_unknown_sensitivity_is_rejected(self):
        with self.assertRaises(model.PersonaError):
            model.Persona(_minimal(sensitivity="enthusiastic"))

    def test_every_sensitivity_level_has_a_cap_and_a_bar(self):
        for level in model.SENSITIVITY_ORDER:
            self.assertIn(level, model.SENSITIVITY)
            self.assertGreater(model.SENSITIVITY[level]["max_per_day"], 0)
            self.assertTrue(model.SENSITIVITY[level]["bar"].strip())

    def test_caps_increase_with_eagerness(self):
        caps = [model.SENSITIVITY[l]["max_per_day"] for l in model.SENSITIVITY_ORDER]
        self.assertEqual(caps, sorted(caps))

    def test_explicit_cap_overrides_the_level_default(self):
        p = model.Persona(_minimal(sensitivity="low", max_per_day=99))
        self.assertEqual(p.max_per_day, 99)

    def test_unknown_collision_kind_is_rejected(self):
        with self.assertRaises(model.PersonaError):
            model.Persona(_minimal(collision_kinds=["correction", "vibes"]))

    def test_promotable_kinds_are_limited_to_what_the_persona_declares(self):
        p = model.Persona(_minimal(collision_kinds=["correction", "constraint"]))
        self.assertEqual(["constraint"], p.promotable_kinds)

    def test_a_persona_can_declare_no_promotable_kinds(self):
        p = model.Persona(_minimal(collision_kinds=["correction", "override"]))
        self.assertEqual([], p.promotable_kinds)

    def test_description_budget_is_reported_not_silently_exceeded(self):
        wordy = _minimal()
        wordy["domain"]["occasions"] = ["a very long occasion clause " * 30]
        p = model.Persona(wordy)
        self.assertGreater(len(p.description()), model.DESCRIPTION_BUDGET_CHARS)
        self.assertTrue(any("over the" in w for w in p.validate()))


class TestShippedPersonas(unittest.TestCase):
    """The personas in the repo must stay valid and within budget."""

    def _personas(self):
        return list((REPO / "personas").glob("*.yaml")) + \
               list((REPO / "examples" / "personas").glob("*.yaml"))

    def test_all_load_and_fit_the_budget(self):
        found = self._personas()
        self.assertTrue(found, "no personas found in the repo")
        for path in found:
            p = model.load(str(path))
            self.assertLessEqual(len(p.description()), model.DESCRIPTION_BUDGET_CHARS,
                                 "%s exceeds the description budget" % path.name)
            self.assertEqual([], [w for w in p.validate() if "over the" in w])


# -- unit: rendering ------------------------------------------------------

class TestRender(unittest.TestCase):

    def setUp(self):
        self.p = model.load(str(COLLISION))

    def test_no_unresolved_tokens_anywhere(self):
        for name, fn in (("skill", render.skill_md), ("new_note", render.new_note_py),
                         ("append_index", render.append_index_py),
                         ("hooks", render.hooks_py), ("readme", render.store_readme)):
            self.assertNotIn("{{", fn(self.p), "%s left an unrendered token" % name)

    def test_generated_python_compiles(self):
        for fn in (render.new_note_py, render.append_index_py, render.hooks_py):
            compile(fn(self.p), "<generated>", "exec")

    def test_unknown_token_raises_rather_than_shipping_a_hole(self):
        with self.assertRaises(KeyError):
            render._render("a {{NOPE}} b", {"OTHER": "x"})

    def test_skill_carries_every_quality_mechanism(self):
        skill = render.skill_md(self.p)
        for expected in ("disagreement", "endorsement", "acceptance",
                         "notes per day", "would this still be true in a year",
                         self.p.restrictive_scope):
            self.assertIn(expected.lower(), skill.lower(), "skill lost %r" % expected)


class TestCliSurface(unittest.TestCase):
    """Every subcommand must at least parse and dispatch.

    A refactor that removes a renderer can leave a command referring to it, and
    nothing else in the suite would notice until someone typed the command.
    """

    def test_every_declared_command_has_a_handler(self):
        parser = cli.build_parser()
        subparsers = [a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction)][0]
        self.assertGreater(len(subparsers.choices), 5)
        for name, sub in subparsers.choices.items():
            self.assertTrue(callable(sub.get_default("fn")),
                            "%s has no handler" % name)

    def test_read_takes_all_notes_or_one(self):
        """One verb narrowed by its argument, rather than a verb plus a flag."""
        parser = cli.build_parser()
        subparsers = [a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction)][0]
        read = subparsers.choices["read"]
        self.assertIsNone(read.parse_args([]).note, "no argument means all of them")
        self.assertEqual("00007", read.parse_args(["00007"]).note)

    def test_git_and_unix_names_keep_their_old_aliases(self):
        parser = cli.build_parser()
        subparsers = [a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction)][0]
        for old, new in (("log", "list"), ("show", "read"), ("grep", "search"),
                         ("rm", "forget"), ("doctor", "status")):
            self.assertIn(old, subparsers.choices, "%s alias vanished" % old)
            self.assertIs(subparsers.choices[old], subparsers.choices[new])

    def test_preview_renders_every_artefact_it_lists(self):
        for attr in ("skill_md", "new_note_py", "append_index_py", "hooks_py",
                     "store_readme"):
            self.assertTrue(hasattr(render, attr), "render lost %s" % attr)


# -- end to end: install, run, uninstall ----------------------------------

class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.profile_dir = self.home / ".claude"
        (self.profile_dir / "skills").mkdir(parents=True)
        (self.profile_dir / "settings.json").write_text("{}", encoding="utf-8")
        self._real_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        # Persona store paths expand at construction, so build after HOME is set.
        self.p = model.load(str(COLLISION))
        self.profile = Profile(self.profile_dir)

    def tearDown(self):
        if self._real_home is not None:
            os.environ["HOME"] = self._real_home
        shutil.rmtree(str(self.home), ignore_errors=True)

    def _settings(self):
        return json.loads((self.profile_dir / "settings.json").read_text(encoding="utf-8"))

    def test_install_writes_every_artefact(self):
        installer.install(self.p, self.profile)
        sdir = installer.skill_dir(self.p, self.profile)
        for name in ("SKILL.md", "new_note.py", "append_index.py"):
            self.assertTrue((sdir / name).exists(), "missing %s" % name)
        self.assertTrue((self.profile_dir / "hooks" / "collision_capture_hooks.py").exists())
        self.assertTrue((Path(self.p.store_path) / "INDEX.md").exists())

    def test_status_accepts_what_install_actually_writes(self):
        """A doctor that fails a healthy install is worse than no doctor.

        The two sides count permission rules independently, so changing one
        without the other reports capture as broken while it works fine.
        """
        installer.install(self.p, self.profile)
        allow = self._settings()["permissions"]["allow"]
        store_rules = [e for e in allow
                       if e.startswith("Edit(") and self.p.store_path in e]
        self.assertEqual(1, len(store_rules))

    def test_install_registers_hooks_and_permissions(self):
        installer.install(self.p, self.profile)
        settings = self._settings()
        for event in ("SessionStart", "PostToolUse", "PreCompact"):
            self.assertIn(event, settings["hooks"], "no %s hook" % event)
        # two Bash rules for the scripts, one Edit rule for the store
        self.assertEqual(3, len(settings["permissions"]["allow"]))

    def test_file_permissions_use_the_absolute_path_form(self):
        installer.install(self.p, self.profile)
        allow = self._settings()["permissions"]["allow"]
        rule = [e for e in allow if e.startswith("Edit(")]
        self.assertEqual(1, len(rule), "no single Edit rule")
        self.assertTrue(rule[0].startswith("Edit(//"),
                        "Edit rule is not absolute: %s" % rule[0])
        # An Edit rule already covers Write; a Write rule matches nothing and
        # the client warns about it every startup.
        self.assertEqual([], [e for e in allow if e.startswith("Write(")])

    def test_install_is_idempotent(self):
        installer.install(self.p, self.profile)
        first = self._settings()
        installer.install(self.p, self.profile)
        self.assertEqual(first, self._settings(), "re-installing changed settings.json")

    def test_uninstall_removes_the_mechanism_but_keeps_the_store(self):
        installer.install(self.p, self.profile)
        note = Path(self.p.store_path) / "000001-kept.md"
        note.write_text("mine", encoding="utf-8")
        installer.uninstall(self.p, self.profile)
        settings = self._settings()
        self.assertEqual([], settings.get("permissions", {}).get("allow", []))
        self.assertEqual({}, settings.get("hooks", {}))
        self.assertFalse(installer.skill_dir(self.p, self.profile).exists())
        self.assertTrue(note.exists(), "uninstall deleted a note")

    def test_upgrading_over_an_older_layout_leaves_one_hook_set(self):
        """An older version registered differently named hooks. Both would fire."""
        settings = self._settings()
        settings.setdefault("hooks", {}).setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command",
                        "command": "bash %s/hooks/collision-capture-sessionstart.sh"
                                   % self.profile_dir}]})
        self.profile.save_settings(settings)
        installer.install(self.p, self.profile)
        after = json.dumps(self._settings())
        self.assertNotIn("-capture-sessionstart.sh", after)
        self.assertIn("collision_capture_hooks.py", after)

    # -- the generated scripts, run as subprocesses ------------------------

    def _run(self, script, *args, **kwargs):
        return subprocess.run([sys.executable, str(script)] + list(args),
                              capture_output=True, text=True,
                              input=kwargs.get("stdin"),
                              env=dict(os.environ, **kwargs.get("env", {})))

    def test_allocator_numbers_in_capture_order(self):
        installer.install(self.p, self.profile)
        alloc = installer.skill_dir(self.p, self.profile) / "new_note.py"
        first = self._run(alloc, "first-note", "disagreement").stdout.strip()
        self.assertTrue(first.endswith("00001-first-note.md"), first)
        Path(first).write_text("x", encoding="utf-8")
        second = self._run(alloc, "second-note", "disagreement").stdout.strip()
        self.assertTrue(second.endswith("00002-second-note.md"), second)

    def test_concurrent_allocators_reserve_distinct_paths(self):
        p = model.Persona(dict(miniyaml.load_file(str(COLLISION)), max_per_day=0),
                          path=str(COLLISION))
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        processes = [subprocess.Popen([sys.executable, str(alloc), "same-slug", "disagreement"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True)
                     for _ in range(8)]
        results = [process.communicate() for process in processes]
        paths = [stdout.strip() for stdout, _ in results]

        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        self.assertEqual(8, len(set(paths)), paths)
        self.assertTrue(all(Path(path).exists() for path in paths))

    def test_daily_cap_holds_across_concurrent_allocators(self):
        p = model.Persona(dict(miniyaml.load_file(str(COLLISION)), max_per_day=2),
                          path=str(COLLISION))
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        processes = [subprocess.Popen([sys.executable, str(alloc), "capped", "endorsement"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True)
                     for _ in range(8)]
        results = [process.communicate() for process in processes]

        self.assertEqual(2, sum(process.returncode == 0 for process in processes), results)
        self.assertTrue(all(process.returncode in (0, 2) for process in processes), results)

    def test_allocator_rejects_a_bad_slug(self):
        installer.install(self.p, self.profile)
        alloc = installer.skill_dir(self.p, self.profile) / "new_note.py"
        result = self._run(alloc, "Not A Slug", "disagreement")
        self.assertNotEqual(0, result.returncode)

    def test_ids_run_into_letters_when_the_decimal_range_is_exhausted(self):
        installer.install(self.p, self.profile)
        alloc = installer.skill_dir(self.p, self.profile) / "new_note.py"
        store = Path(self.p.store_path)
        (store / "99999-last-decimal.md").write_text("x", encoding="utf-8")
        nxt = Path(self._run(alloc, "first-letter", "disagreement").stdout.strip()).name
        self.assertTrue(nxt.startswith("A0000-"), nxt)
        # '9' sorts before 'A', so capture order survives a plain sort.
        self.assertLess("99999-last-decimal.md", nxt)

    def test_allocator_enforces_the_daily_cap(self):
        p = model.Persona(dict(miniyaml.load_file(str(COLLISION)), max_per_day=2),
                          path=str(COLLISION))
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        self.assertEqual(0, self._run(alloc, "one", "endorsement").returncode)
        self.assertEqual(0, self._run(alloc, "two", "endorsement").returncode)
        refused = self._run(alloc, "three", "endorsement")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("cap", refused.stderr.lower())

    def test_strong_evidence_is_never_rationed(self):
        p = model.Persona(_minimal(sensitivity="minimal"), path="x.yaml")
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        # A cap of one, then four more collisions. None of them may be refused.
        for n in range(5):
            result = self._run(alloc, "collision-%d" % n, "disagreement")
            self.assertEqual(0, result.returncode, result.stderr)

    def test_weak_evidence_still_meets_the_cap(self):
        p = model.Persona(_minimal(sensitivity="minimal"), path="x.yaml")
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        self.assertEqual(0, self._run(alloc, "one", "endorsement").returncode)
        refused = self._run(alloc, "two", "endorsement")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("cap", refused.stderr.lower())

    def test_free_evidence_does_not_spend_the_cap_for_weak_evidence(self):
        p = model.Persona(_minimal(sensitivity="minimal"), path="x.yaml")
        installer.install(p, self.profile)
        alloc = installer.skill_dir(p, self.profile) / "new_note.py"
        for n in range(3):
            self._run(alloc, "collision-%d" % n, "disagreement")
        # The day's one counted slot is still untouched.
        self.assertEqual(0, self._run(alloc, "weak", "endorsement").returncode)

    def test_an_acceptance_is_refused_outright(self):
        installer.install(self.p, self.profile)
        alloc = installer.skill_dir(self.p, self.profile) / "new_note.py"
        refused = self._run(alloc, "mere-agreement", "acceptance")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("not a collision", refused.stderr.lower())
        self.assertEqual([], list(Path(self.p.store_path).glob("*mere-agreement*")))

    def test_allocator_rejects_unknown_evidence(self):
        installer.install(self.p, self.profile)
        alloc = installer.skill_dir(self.p, self.profile) / "new_note.py"
        self.assertNotEqual(0, self._run(alloc, "a-slug", "vibes").returncode)

    def _note(self, number, slug, date="2026-01-01", evidence="disagreement"):
        store = Path(self.p.store_path)
        store.mkdir(parents=True, exist_ok=True)
        (store / ("%s-%s.md" % (number, slug))).write_text(
            "---\nname: %s\ncaptured: %s\nevidence: %s\n---\n\nThe rule.\n\n"
            "**Why:** what breaks without it\n\n**Origin:** where it came from\n"
            % (slug, date, evidence), encoding="utf-8")

    def test_index_line_carries_kind_and_scope(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-slug")
        self._run(index, "00001", "2026-01-01", "a-slug", "universal", "constraint", "disagreement", "hook")
        line = (Path(self.p.store_path) / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("`constraint`", line)
        self.assertIn("`universal`", line)

    def test_index_lines_written_before_evidence_still_read(self):
        store = Path(self.p.store_path)
        store.mkdir(parents=True, exist_ok=True)
        (store / "INDEX.md").write_text(
            "- `00001` - `2026-01-01` - [old](00001-old.md) - `constraint` - "
            "`universal` - a rule from before\n"
            "- `00002` - `2026-01-02` - [new](00002-new.md) - `constraint` - "
            "`universal` - `disagreement` - a rule from after\n", encoding="utf-8")
        rows = cli._index_rows(store)
        self.assertEqual(["-", "disagreement"], [r["evidence"] for r in rows])

    def _bare_note(self, number, slug, body):
        store = Path(self.p.store_path)
        store.mkdir(parents=True, exist_ok=True)
        (store / ("%s-%s.md" % (number, slug))).write_text(
            "---\nname: %s\ncaptured: 2026-01-01\nevidence: disagreement\n---\n\n%s"
            % (slug, body), encoding="utf-8")

    def _index(self, slug):
        return self._run(installer.skill_dir(self.p, self.profile) / "append_index.py",
                         "00001", "2026-01-01", slug, "universal", "constraint",
                         "disagreement", "hook")

    def test_a_note_without_its_reasons_is_refused(self):
        installer.install(self.p, self.profile)
        self._bare_note("00001", "no-reason", "The rule and nothing else.\n")
        refused = self._index("no-reason")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("**Why:**", refused.stderr)

    def test_an_empty_reason_line_is_refused(self):
        installer.install(self.p, self.profile)
        self._bare_note("00001", "empty-why",
                        "The rule.\n\n**Why:**\n\n**Origin:** somewhere\n")
        refused = self._index("empty-why")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("empty", refused.stderr.lower())

    def test_a_missing_origin_is_refused(self):
        installer.install(self.p, self.profile)
        self._bare_note("00001", "no-origin", "The rule.\n\n**Why:** it breaks\n")
        refused = self._index("no-origin")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("**Origin:**", refused.stderr)

    def test_reasons_are_found_beyond_the_frontmatter_window(self):
        installer.install(self.p, self.profile)
        # The frontmatter check reads a short head; the reasons sit well past it.
        self._bare_note("00001", "long-note",
                        "The rule. " + ("padding " * 120) +
                        "\n\n**Why:** it breaks\n\n**Origin:** they said so\n")
        self.assertEqual(0, self._index("long-note").returncode)

    def test_index_line_carries_the_evidence(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-slug", evidence="endorsement")
        self._run(index, "00001", "2026-01-01", "a-slug", "universal", "constraint",
                  "endorsement", "hook")
        line = (Path(self.p.store_path) / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("`endorsement`", line)

    def test_index_refuses_evidence_the_note_does_not_carry(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-slug", evidence="endorsement")
        drifted = self._run(index, "00001", "2026-01-01", "a-slug", "universal",
                            "constraint", "disagreement", "hook")
        self.assertNotEqual(0, drifted.returncode)
        index_md = (Path(self.p.store_path) / "INDEX.md").read_text(encoding="utf-8")
        self.assertNotIn("a-slug", index_md)

    def test_index_rejects_an_unknown_evidence(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "s")
        bad = self._run(index, "00001", "2026-01-01", "s", "universal", "constraint",
                        "vibes", "h")
        self.assertNotEqual(0, bad.returncode)

    def test_index_rejects_an_unknown_kind_or_scope(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "s")
        bad_kind = self._run(index, "00001", "2026-01-01", "s", "universal", "vibes", "disagreement", "h")
        bad_scope = self._run(index, "00001", "2026-01-01", "s", "cosmic", "constraint", "disagreement", "h")
        self.assertNotEqual(0, bad_kind.returncode)
        self.assertNotEqual(0, bad_scope.returncode)

    def _candidates(self):
        path = Path(self.p.store_path) / "CANDIDATES.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_a_promotable_kind_is_shortlisted(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-rule")
        self._run(index, "00001", "2026-01-01", "a-rule", "universal", "constraint", "disagreement", "hook")
        self.assertIn("a-rule", self._candidates())

    def test_the_shortlist_skips_one_off_kinds_and_the_restrictive_scope(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-correction")
        self._note("00002", "a-private-rule")
        # A correction is tied to what it corrected, so it never generalises.
        self._run(index, "00001", "2026-01-01", "a-correction", "universal", "correction", "disagreement", "h")
        # The most restrictive scope never leaves the store, shortlist included.
        self._run(index, "00002", "2026-01-01", "a-private-rule", "private", "constraint", "disagreement", "h")
        self.assertEqual("", self._candidates())

    def test_shortlisting_never_moves_a_note(self):
        installer.install(self.p, self.profile)
        index = installer.skill_dir(self.p, self.profile) / "append_index.py"
        self._note("00001", "a-rule")
        self._run(index, "00001", "2026-01-01", "a-rule", "universal", "constraint", "disagreement", "hook")
        self.assertTrue((Path(self.p.store_path) / "00001-a-rule.md").exists())

    def _hooks(self):
        return self.profile_dir / "hooks" / "collision_capture_hooks.py"

    def test_arm_hook_emits_session_context(self):
        installer.install(self.p, self.profile)
        out = json.loads(self._run(self._hooks(), "arm").stdout)
        self.assertEqual("SessionStart", out["hookSpecificOutput"]["hookEventName"])
        self.assertIn(self.p.skill, out["hookSpecificOutput"]["additionalContext"])

    def test_sweep_hook_emits_precompact_context(self):
        installer.install(self.p, self.profile)
        out = json.loads(self._run(self._hooks(), "sweep").stdout)
        self.assertEqual("PreCompact", out["hookSpecificOutput"]["hookEventName"])

    def test_notify_reports_a_note_and_ignores_everything_else(self):
        installer.install(self.p, self.profile)
        note = os.path.join(self.p.store_path, "000001-cache-needs-a-version.md")
        payload = json.dumps({"tool_input": {"file_path": note}})
        out = json.loads(self._run(self._hooks(), "notify", stdin=payload).stdout)
        self.assertIn("cache needs a version", out["systemMessage"])

        for ignored in (os.path.join(self.p.store_path, "README.md"),
                        os.path.join(self.p.store_path, "INDEX.md"),
                        "/tmp/unrelated.md"):
            payload = json.dumps({"tool_input": {"file_path": ignored}})
            self.assertEqual("", self._run(self._hooks(), "notify", stdin=payload).stdout.strip(),
                             "notify fired on %s" % ignored)

    def test_notify_survives_junk_on_stdin(self):
        installer.install(self.p, self.profile)
        result = self._run(self._hooks(), "notify", stdin="not json at all")
        self.assertEqual(0, result.returncode)

    def test_env_kill_switch_silences_every_hook(self):
        installer.install(self.p, self.profile)
        for mode in ("arm", "sweep"):
            out = self._run(self._hooks(), mode, env={"PROMPTIE_DISABLE": "1"})
            self.assertEqual("", out.stdout.strip(), "%s spoke while disabled" % mode)

    def test_ignore_file_silences_hooks_from_a_subdirectory(self):
        installer.install(self.p, self.profile)
        project = self.home / "project" / "deep" / "nested"
        project.mkdir(parents=True)
        (self.home / "project" / ".promptieignore").write_text("", encoding="utf-8")
        out = subprocess.run([sys.executable, str(self._hooks()), "arm"],
                             capture_output=True, text=True, cwd=str(project))
        self.assertEqual("", out.stdout.strip())

    def test_project_focus_is_appended_to_the_arming_line(self):
        installer.install(self.p, self.profile)
        project = self.home / "project"
        project.mkdir()
        (project / ".promptie").write_text("capture routing decisions", encoding="utf-8")
        out = subprocess.run([sys.executable, str(self._hooks()), "arm"],
                             capture_output=True, text=True, cwd=str(project))
        context = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("capture routing decisions", context)


# -- portability ----------------------------------------------------------

class TestPortability(unittest.TestCase):
    """Guards on the promises the README makes about other platforms."""

    def _generated_sources(self):
        p = model.load(str(COLLISION))
        return {"new_note.py": render.new_note_py(p),
                "append_index.py": render.append_index_py(p),
                "hooks.py": render.hooks_py(p)}

    def test_generated_runtime_imports_only_the_standard_library(self):
        allowed = {"datetime", "io", "json", "os", "re", "sys"}
        for name, source in self._generated_sources().items():
            for line in source.splitlines():
                if line.startswith("import "):
                    module = line.split()[1].split(".")[0]
                    self.assertIn(module, allowed, "%s imports %s" % (name, module))

    def test_generated_runtime_shells_out_to_nothing(self):
        for name, source in self._generated_sources().items():
            for banned in ("subprocess", "os.system", "jq ", "bash "):
                self.assertNotIn(banned, source, "%s depends on %s" % (name, banned))

    def test_paths_are_joined_not_concatenated(self):
        # os.path.join and pathlib both behave on Windows; "a" + "/" + "b" does not.
        for name, source in self._generated_sources().items():
            self.assertNotIn('+ "/" +', source, "%s builds a path by hand" % name)

    def test_files_are_opened_with_an_explicit_encoding(self):
        # The platform default is not UTF-8 on Windows, and notes contain prose.
        for name, source in self._generated_sources().items():
            for line in source.splitlines():
                if ".open(" in line or line.strip().startswith("with open("):
                    if '"r"' in line or '"a"' in line or '"w"' in line:
                        self.assertIn("encoding=", line, "%s: %s" % (name, line.strip()))

    def test_interpreter_baked_into_hooks_is_absolute(self):
        self.assertTrue(os.path.isabs(installer.runtime_python()))


if __name__ == "__main__":
    unittest.main()
