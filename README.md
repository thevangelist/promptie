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
promptie log          # your notes, oldest first
promptie show 00007   # read one
promptie grep cache   # search them
promptie rm 00007     # prune one, index rebuilt
promptie status       # is capture actually live
```

The verbs are git's and unix's on purpose. This is an append-only, ordered store,
so it may as well read like one.

Too much or too little:

```sh
promptie config                          # what is set now
promptie config sensitivity low          # 3 a day
promptie config sensitivity extreme      # 40 a day
```

Five levels: `minimal` 1, `low` 3, `normal` 6, `high` 12, `extreme` 40 notes a day.
The level sets the judgement bar written into the skill; the cap is enforced by the
allocator, which refuses and says so rather than writing quietly.

Off, when a session is throwaway:

```sh
export PROMPTIE_DISABLE=1     # this shell
touch .promptieignore         # this directory tree
```

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
