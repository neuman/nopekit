# Lenses: attacking a firmware build before the board ships

Each lens is a question a reviewer asks of THIS firmware, with the evidence it would
take to close it. A green replay answers none of them.

## The build CI never ran

- When did the CI job last go green on GitHub? Open the run, not the badge. A
  workflow that nobody reads can stay red for many revisions.
- Is the job `fw.ci_job` chose the job that makes the file you flash? A second
  workflow, a release job or a build on someone's laptop can make another file.

## The green tick on a masked failure

- Does any step after the build hide its status: a `tee`, a `grep`, an `|| true`,
  a script that ends with `echo done`? `fw.ci_job` reads the build step's own lines
  and, where they hold no build command, the shell scripts they run. It names each
  other file the step runs as not read, and it skips a build inside an action or a
  Python script. Open each one.
- Does a later step upload a file a failed build left behind?

## The region nobody confirmed

- Which document gives `app_memory_map`: the bootloader's own layout, the part's
  datasheet, the partition table that is flashed? Who read it?
- Is the bootloader on the device the one that document describes? A board that
  ships with another bootloader version can start the app elsewhere.

## The OTA slot after next year's features

- How much headroom does the claim state, and why that much? The image grows with
  every release; the slot does not.
- Does the update path ever write into the smallest slot?

## The wake pin the board routed

- Is the wake button on a pin that can wake the chip from deep sleep (an ESP32 RTC
  GPIO, an STM32 WKUP pin)? The image cannot show it. Sleep a real board and press
  the button.
- Does every path to the sleep call arm the wake first?

## The sleep current nobody measured

- What does the board draw in deep sleep? A peripheral left on drains the cell in a
  week, and no static reading sees it. Measure it and record a physical claim.

## The setting the grant was tested at

- Which setting does the module's test report or integration manual give for its
  grant? Is that the number in the `ble-tx-power` claim, and not the grant's listed
  output?
- Does anything raise the power at run time: a phone app, a host over HCI, a library
  call? The build shows the default and the paths it can see, not every caller.
- Did the antenna, its feed or a front-end module change since the grant? A setting
  inside the grant says nothing of a new antenna's gain. Measure it and record a
  physical claim.

## The hang with no reset path

- If the firmware hangs in a sealed device, what resets it? A watchdog that is
  enabled but never fed, or fed from the wrong task, is no reset path.

## The dependency that floats

- Does the build fetch anything at a branch or a moving tag (a west manifest
  revision, a component manager range, a `:stable` image)? Two builds of one tree
  can then differ, and a replay cannot tell which one CI made.
