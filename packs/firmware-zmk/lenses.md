# Lenses: attacking a firmware build before the board ships

Each lens is a question a reviewer asks of THIS firmware, with the evidence it would
take to close it. A green build answers none of them.

## The build CI never ran

- When did the CI job last go green? Open the run, not the badge. A workflow that
  nobody reads can stay red for months.
- Is the step that `zmk.ci_step` read the step that makes the file you flash? A
  second workflow, a manual build or a release job can make another file.

## A keymap nobody wrote

- Which keymap did the build use? Read the `Using keymap file:` line in the
  build's log. A wrong ZMK_CONFIG builds ZMK's default keymap with exit 0.
- Does each key do what the legend on the keycap says? The build cannot know.

## A board that cannot wake

- With deep sleep on, can a key wake the board? Sleep it on the bench and press a
  key. The gate reads the devicetree, not the pins.
- Does any other device (an encoder, a pointing sensor) need to wake the board?
  Only the kscan is judged.
- Is soft off built? Its wakeup sources are another list, and this pack does not
  read it.

## A power level nobody granted

- Which grant covers the radio module you bought, and at what conducted power? A
  person reads the grant and writes the claim.
- Does the antenna or the enclosure add gain past what the grant tested?
- Can a setting at run time raise the power? `zmk.tx_power` fails on dynamic
  control, and reads nothing the firmware does after boot.

## The image that is not the one pinned

- CI runs `zmk-build-arm:stable`. When did that tag last move? Pull it and compare
  its digest with the one in `bin/zmk-west`.
- Does CI's manifest still pin `v0.3.0`? A floating revision gives other firmware
  each day.
