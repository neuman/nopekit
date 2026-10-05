# Contributing

The most valuable contribution is a **pack** — a reusable validation capability for
one physical domain. The second most valuable is a **bug in a gate**, especially one
where the gate was reporting a confident pass.

Read [`METHOD.md`](METHOD.md) first. It is ten rules and it is the whole system.

## The one rule that decides a merge

**A gate must be shown to fail on input that is known to be bad.**

Every gate declares a negative control, the registry refuses to register one without
it, and CI runs every control — `gate selftest` at the repo root demonstrates every
bundled pack — and fails any gate that passes its own known-bad fixture. This is not
bureaucracy: an agent that writes plausible code will write plausible validators, and
a plausible validator is worse than none, because it launders assumption into
apparent proof.

A pack whose gates have never demonstrated failure does not get merged.

## Contributing a pack

Most good packs are **extractions**, not inventions — code that already survived
contact with a real build.

1. Follow [`docs/EXTENSION_PROTOCOL.md`](docs/EXTENSION_PROTOCOL.md) if the pack came
   out of a real capability gap. A pack is step 7 of that protocol, not step 1.
2. Lay it out and write it against [`docs/PACK_FORMAT.md`](docs/PACK_FORMAT.md). A pack
   is an ordinary directory; no command creates one for you.
3. Ship at least one **tier-0** gate (analytic, closed form, sub-second). A pack of
   only expensive gates catches nothing while the design is still cheap to change.
4. Ship a **validity guard** — the gate that decides whether the pack's other numbers
   mean anything (slenderness for beam theory, Biot for lumped capacitance, Reynolds
   for a correlation). It is usually the most valuable gate in the pack.
5. Write `selftest/baseline.json`: a plausible, physically coherent projection that
   **every** gate passes. Fixtures then move one thing and the gate must flip to FAIL.
6. **Seal your fixtures.** Build the bad context from your own baseline, never from
   `ctx.params`. A control whose severity depends on the host project passes in some
   repositories and fails in others.
7. Be honest in `PACK.md` about **what the pack cannot settle**. That section prevents
   more damage than the gate list.

```sh
PYTHONPATH=src python3 -m nopekit pack validate <name>
PYTHONPATH=src python3 -m nopekit gate selftest --pack <name> --junit <file>.xml
PYTHONPATH=src python3 -m unittest discover -s tests
```

All three green, then open a PR with the JUnit file attached: it is the admission
evidence, gate by gate, at every tier — each control that fired on its known-bad
input, and each that could not run for want of a tool, which is reported as a skip
and never counted as fired. `pack validate` demonstrates tiers 0–1 only, so it stays
seconds long.

## Contributing to the spine

`src/nopekit/` is **standard library only**. No third-party imports, ever — the
spine must never be the reason an install fails. CI proves it by AST-walking every
import, including function-local ones.

The honesty invariants in [`CLAUDE.md`](CLAUDE.md) have tests that actively try to
violate them. If your change makes one of those tests need a weaker assertion,
the change is wrong.

## Reporting a bad gate

The highest-signal bug report here is:

> gate `X` returned a pass on input where the real answer is a failure

Include the projection you gave it and what the true answer is, with a source. A gate
that is wrong in the conservative direction is a nuisance; a gate that is wrong in the
optimistic direction is the thing this project exists to prevent.

## Style

- Every constant carries its provenance: why this value, and what was tried and
  rejected. The rejected alternatives are the part that pays.
- Comments say what slipped through. Where a rule exists because something got past a
  check, name it.
- One dense line in a verdict's `detail`; bulk output goes to `ctx.out_dir`.
- No reference to the parent project — see [`docs/ORIGINS.md`](docs/ORIGINS.md).

## Licence

Apache 2.0. By contributing you agree your work ships under it.
