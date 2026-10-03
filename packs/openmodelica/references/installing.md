# Installing OpenModelica, pinned, with a smoke test

Read `docs/EXTENSION_PROTOCOL.md` step 4 first. The rule that matters here: **a
mis-built solver produces plausible numbers**, and a plausible number is worse
than an error. Establish that the tool works on a case with a known answer before
you trust it on a case without one.

## What it costs, said out loud

| | |
|---|---|
| Disk, compiler only (`omc`) | ~700 MB – 1 GB |
| Disk, full install with libraries and the GUI | ~2–3 GB |
| Install time | 3–10 min on a decent connection |
| First `checkModel` on a fresh machine | 10–60 s (it loads and indexes the library tree) |
| Second `checkModel` on the same model | 1–5 s |
| `simulate` of a small model | 5–30 s, most of it the C compile of generated code |

That last row is why `modelica.simulates` is tier 2 and the result-reading gates
are tier 0. If the design is still moving, record one run and iterate against the
tier-0 half; reach for the compiler when the equations change.

## Linux (apt) — the usual answer on Debian/Ubuntu/WSL

```bash
sudo apt update && sudo apt install -y ca-certificates curl gnupg lsb-release

curl -fsSL https://build.openmodelica.org/apt/openmodelica.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/openmodelica-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/openmodelica-keyring.gpg] \
https://build.openmodelica.org/apt $(lsb_release -cs) release" \
  | sudo tee /etc/apt/sources.list.d/openmodelica.list

sudo apt update
sudo apt install -y omc                 # the compiler; MSL is a separate install, below
# sudo apt install -y openmodelica      # ...or the metapackage, with OMEdit and everything
```

Pin the version you actually installed, and record it:

```bash
apt-cache policy omc                    # what is available
sudo apt install -y omc=1.22.1-1        # example — substitute the version you checked
apt-mark hold omc                       # so an unrelated `apt upgrade` cannot move it
```

The repository also carries `nightly` and `stable` suites in place of `release`.
**Do not use `nightly` for a gate.** A validator whose tool changes underneath it
between two runs cannot gate anything.

## Docker — the reproducible option, and the one to prefer in CI

```bash
docker pull openmodelica/openmodelica:v1.22.0-minimal      # ~1.1 GB, no GUI
docker run --rm openmodelica/openmodelica:v1.22.0-minimal omc --version
```

Every command on this page was run against that tag. Pin a different one if you
like, then rerun the smoke test and `msl-check.mos` (below) on it.

**`-minimal` is the compiler with no Modelica libraries.** There is no
`package.mo` anywhere in the `v1.22.0-minimal` image, and `loadModel(Modelica)`
returns `false`. omc then tries to download the library and cannot, because a
container run as `-u "$(id -u):$(id -g)"` has `HOME=/`, and that user cannot
write there:

```
Error: Failed to open file for writing: //.openmodelica/libraries/index.json.tmp1
Error: Failed to download package index https://libraries.openmodelica.org/index/v1/index.json to file //.openmodelica/libraries/index.json.
Error: Failed to load package Modelica (default) using MODELICAPATH //.openmodelica/libraries/.
```

A model that uses nothing from the library runs on the bare image. The pack's own
fixtures are written that way. A model that touches `Modelica.*` cannot be
checked, built or simulated until the library is installed, and that needs a
`HOME` the container can write and that outlives `--rm`: a named volume.

Once, before anything else:

```bash
docker volume create omc-home
# A fresh volume's root belongs to root. Hand it to yourself, or omc cannot write it.
docker run --rm -v omc-home:/omhome openmodelica/openmodelica:v1.22.0-minimal \
  chown "$(id -u):$(id -g)" /omhome
```

To let the gates use it, put an `omc` on PATH that forwards into the container.
It has to get two things right:

1. **Paths.** The gates write their `.mos` under `<out_dir>/omc` (by default
   `<project>/.atompipe/out/omc`, and a temp directory under `gate selftest`).
   They run omc from there and pass it **absolute paths** to sources that live
   somewhere else. If the wrapper mounts only `$PWD`, every `loadFile` returns
   `false` and the gate FAILs a model nobody read. So mount the trees your
   projects and your temp directory live in, at the same paths inside the
   container.
2. **Libraries.** Point `HOME` at the volume. Then `installPackage` has somewhere
   to write, and every later run finds what it wrote.

```bash
#!/usr/bin/env bash
# ~/bin/omc — omc-in-docker, pinned, with its libraries in the omc-home volume
set -euo pipefail
IMAGE=openmodelica/openmodelica:v1.22.0-minimal
tmp=${TMPDIR:-/tmp}
# If all your projects live under one directory, mount that instead of $HOME.
mounts=(-v omc-home:/omhome -e HOME=/omhome -v "$HOME:$HOME")
[ "$tmp" != "$HOME" ] && mounts+=(-v "$tmp:$tmp")
case "$PWD/" in "$HOME"/*|"$tmp"/*) ;; *) mounts+=(-v "$PWD:$PWD") ;; esac
exec docker run --rm -u "$(id -u):$(id -g)" "${mounts[@]}" -w "$PWD" "$IMAGE" omc "$@"
```

Then install the library once. This is the only step that needs the network:

```bash
cat > install-msl.mos <<'EOF'
installPackage(Modelica, "4.0.0+maint.om", exactMatch=true);
getErrorString();
EOF
omc install-msl.mos
```

It prints `true`, then three `Package installed successfully` notifications
(ModelicaServices, Complex, Modelica). That is about 74 MB in the volume and ten
seconds. Check it by version:

```bash
cat > msl-check.mos <<'EOF'
loadModel(Modelica, {"4.0.0"});
getErrorString();
getVersion(Modelica);
EOF
omc msl-check.mos                       # true, then "", then "4.0.0"
```

For a stronger check, add `--network none` to the wrapper's `docker run` for one
run. The same three lines show that nothing reaches for the network any more.

*Rejected:* `-e HOME="$HOME"` (the container writing into your real home). It
also gives omc a writable home, but it puts the container's libraries in the
`~/.openmodelica` that a native omc of another version reads too. The volume
keeps the libraries next to the image they were installed for.

The tag suffixes: `-minimal` is the compiler with no libraries (above).
`-ompython` adds the Python bindings, which this pack deliberately does not use.
`-gui` adds OMEdit and is much larger. Don't assume from a tag's name that it
bundles MSL. Run `msl-check.mos` to find out.

## macOS

Homebrew has no maintained formula. Use the official `.pkg` from
<https://openmodelica.org/download/download-mac/>, or Docker as above. The `.pkg`
puts `omc` in `/Applications/OpenModelica.app/Contents/Resources/opt/bin/omc`;
symlink it onto PATH rather than teaching the pack a new path — `requires_tools`
is `omc` and it is found with `shutil.which`.

## Windows

The official installer from <https://openmodelica.org/download/download-windows/>
puts `omc.exe` in `C:\Program Files\OpenModelica<version>\bin`, which is NOT on
PATH by default. Under WSL, install the Linux package inside the WSL distribution
instead of trying to reach the Windows binary: the `.mos` scripts carry POSIX
paths and `omc.exe` will not resolve them.

## The Modelica Standard Library

Assume omc has no libraries until `msl-check.mos` (above) says otherwise. The
`-minimal` docker image has none, and the `omc` package is the compiler.
omc's package manager installs into `$HOME/.openmodelica/libraries`, so the user
omc runs as needs a writable `HOME`. Under docker, that is what the `omc-home`
volume is for. A native install uses the same two scripts as the docker
section: `install-msl.mos` once, then `msl-check.mos`.

omc reads a script from a **file**, not from stdin. `omc <<'EOF' ... EOF`
prints omc's usage and does nothing, and this page used to recommend exactly
that. `getAvailablePackageVersions(Modelica, "")` lists what the package
*index* offers, installed or not, so it does not tell you what is installed.

The MSL version is a **constant with provenance** exactly like a material
property. MSL 3.2.3 and 4.0.0 differ in package names (`Modelica.SIunits` became
`Modelica.Units.SI`), in several component parameterisations, and in a few
default values. Record in the model's own provenance which one the model was
written and checked against. `modelica_load_libraries` takes the library *name*
(`["Modelica"]`), so install only the version you pinned.

### What a missing library looks like to the gates

When a library listed in `modelica_load_libraries` does not load, the three
tier-2 gates **SKIP** and their claims read BLOCKED, the same as for a missing
omc. The reason names the library and omc's own message:

```
library not loaded, nothing checked: loadModel(Modelica) = false; omc: Failed to load package Modelica (default) using MODELICAPATH //.openmodelica/libraries/. (references/installing.md)
```

In two cases the gates cannot tell a missing library from a defect in the model,
and they read FAIL:

- **The model uses `Modelica.*` but doesn't list it.** omc tries to load the
  library automatically, fails, and reports `Class Modelica.… not found in
  scope`. A misspelt class gives the same message.
- **The model has a `uses(Modelica(...))` annotation but no list entry.** The
  failed load reports errors after `loadFile(...) = true`, and the gate reads
  "the sources did not load".

**List every library the model uses.** The list is what lets the gate tell an
incomplete machine from a broken model.

## The smoke test — run this before any project data

Do not skip it, and do not accept "it printed a version number" as a pass. The
point is a case with a **known answer**.

```bash
mkdir -p /tmp/omc-smoke && cd /tmp/omc-smoke
cat > Smoke.mo <<'EOF'
model Smoke "First-order decay with a closed-form answer"
  parameter Real tau(unit = "s") = 1 "Time constant";
  Real x(start = 1, fixed = true, unit = "1") "State";
equation
  tau*der(x) = -x;
end Smoke;
EOF
cat > smoke.mos <<'EOF'
loadFile("Smoke.mo");
getErrorString();
checkModel(Smoke);
simulate(Smoke, stopTime=1.0, numberOfIntervals=10, outputFormat="csv");
getErrorString();
EOF
omc smoke.mos
tail -1 Smoke_res.csv
```

Three things must be true, and all three are checkable by eye:

1. `checkModel` says **`has 1 equation(s) and 1 variable(s)`**. Equal counts.
2. `simulate` echoes a record with a non-empty `resultFile` and empty-ish
   `messages`.
3. The final row of `Smoke_res.csv` is `t = 1` and `x ≈ 0.367879` — which is
   `exp(-1)`, to as many digits as the tolerance allows. **That is the known
   answer.** A build that produces 0.36 or 0.4 here is a build to throw away.

Keep the output. It is the evidence for extension-protocol step 4, and it belongs
in the ledger next to the decision to install.

## Verifying the pack sees it

```bash
atompipe gate list | grep modelica        # the tier-2 rows stop saying BLOCKED
atompipe gate selftest modelica.checks    # the control must now FIRE, not skip
```

That second command is the one that matters. Until it reports the gate correctly
failing on `selftest/assets/bad/UnbalancedTank.mo`, the tier-2 half of this pack
is installed but unproven.

**A green selftest does not mean MSL is installed.** The pack's fixtures declare
their own SI types and load no library (`modelica_load_libraries: []` in
`selftest/baseline.json`). That is deliberate: it lets the controls run, and fail
as they must, on a bare omc. It also means `gate selftest` passes on an omc that has no MSL at all,
which is why nothing objected while this page said `-minimal` shipped "compiler
and libraries". The selftest checks the compiler, and `msl-check.mos` checks the
library. Run both.

