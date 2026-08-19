![promptie: disagreement is data](cover.png)

# promptie

Every time you correct an AI assistant, you hand it something it did not have. Then the
session ends and it is gone.

promptie keeps it.

## Why

You already know things the model does not. That knowledge surfaces in one recognisable
moment: you read what it produced, and you say no. A constraint it could not have
inferred. A reason the obvious approach fails here. A rule your field learned the hard
way.

That is the only domain signal a model cannot generate for itself, and nobody records it.

## What it is for

- **Turning corrections into instructions.** Review a week of notes, promote the rules
  that repeat into your `CLAUDE.md`, style guide, or onboarding doc.
- **Capturing a domain nobody wrote down.** Field knowledge in logistics, fabrication,
  clinical work, law. The parts that never reached documentation.
- **Recording decisions with their reasons.** The reasoning outlives the decision, and it
  is what nobody can reconstruct a year later.
- **Seeing your own friction.** Ten corrections on one topic in a week means something
  upstream needs fixing.
- **Building a personal corpus.** Plain markdown on your disk. Export it and do what you
  like with it.

The subject does not matter. A collision in a slide deck, a 3D print or a budget has the
same shape as one in code.

## Install

```sh
pipx install git+https://github.com/thevangelist/promptie
promptie onboard
```

`onboard` finds your Claude config directories, asks two questions, installs, then walks
you through capturing one real note. Start a new Claude session afterwards.

Python 3.9 or newer. No other dependencies at all: the generated hooks and
scripts are standard library only. macOS, Linux, BSD, Windows.

## Then nothing

You prompt normally. When a capture happens you see one line:

```
🧠 cache invalidation needs a version
```

Whenever you feel like it:

```sh
promptie list         # one line per note, oldest first
promptie read         # read them all, in order
promptie read 00007   # read one
promptie search cache # find the ones that mention it
promptie forget 00007 # prune one, index rebuilt
promptie status       # is capture actually live
```

Plain verbs, and the argument narrows the verb rather than a flag doing it. `read`
with nothing reads everything.

Too much or too little:

```sh
promptie config                          # what is set now
promptie config sensitivity low          # 3 a day
promptie config sensitivity extreme      # 40 a day
```

Five levels: `minimal` 1, `low` 3, `normal` 6, `high` 12, `extreme` 40 notes a day.
The level sets the judgement bar written into the skill; the cap is enforced by the
allocator, which refuses and says so rather than writing quietly.

The cap prices the evidence rather than counting the notes. A correction you actually
pushed back on is never rationed, however full the day already is. A bare "yes, good" is
refused outright, because agreement is not a collision. Only the middle case spends the
cap: approval that carries a reason.

Off, when a session is throwaway:

```sh
export PROMPTIE_DISABLE=1     # this shell
touch .promptieignore         # this directory tree
```

## Personas

A persona is one YAML file describing what a store is for: the occasions worth
capturing, what to skip, the three scope rungs, how eagerly to write, and the wording of
the skill itself. Everything installed is rendered from it: the skill, the hooks, the
scripts, the permissions. Nothing is hand-written.

You are unlikely to need a second one. `collision` is the default and it declares no
subject at all: the criterion is the disagreement, so it works the same in code, in a
slide deck or in a budget.

Reach for a second persona when you want a **separate store**: its own directory, its
own vocabulary, its own scope gate, its own daily cap. Client work kept apart from
personal notes, say, or a domain whose private rung means something stricter.

```sh
promptie personas            # what is available
promptie check finance       # validate one, and measure its context cost
promptie preview finance     # print what it would install, without installing
promptie install finance     # its own skill, its own store
```

Every command takes a persona name as its first argument, defaulting to `collision`, so
`promptie list finance` reads that store and nothing else.

To write one, copy [`examples/personas/personal-finance-planner.yaml`](examples/personas/personal-finance-planner.yaml)
into `~/.config/promptie/personas/` and edit it. That directory is searched first, so a
file there shadows a packaged persona of the same name. `promptie check` tells you
whether it is valid and whether its description fits the context budget it is charged
against on every turn.

## What it will never do

- **Read the store back.** Notes are unmoderated. Letting them feed into a live session
  would put unvetted claims back into real work. Write-only, in both directions.
- **Publish anything.** Nothing syncs, nothing phones home, and moving a note into a
  shared library is always your manual step.
- **Capture what you marked private.** Every note passes a scope gate, and the most
  restrictive rung never leaves the store.

## How it works

Open the repo in Claude and ask. It is a few hundred lines, every generated file is
rendered from `templates/`, and the reasoning is in the comments where it belongs.

## Licence

[MIT](LICENSE).
