# Installing the tools

The tier-0 gates (`pcb.board_readable`, `pcb.pin_names`, `pcb.net_width`) need
nothing. `pcb.drc`, `pcb.unconnected`, `pcb.erc`, `pcb.parity` and `pcb.fab_rules`
need `kicad-cli` from KiCad 10, 9 or 8 on PATH; `pcb.spice` needs `ngspice`;
`pcb.enclosure_fit` needs `kicad-cli` and the Python module `cadquery`;
`pcb.antenna_keepout` needs `kicad-cli` and KiCad's own Python module `pcbnew`,
importable by the Python that runs nopekit. Without its tool a gate reads
Skipped, never passed.

## kicad-cli

KiCad 10 is the current release. Check: `kicad-cli version` prints `10.0.x` (or
`9.0.x`). The gates were measured on 9.0.9 and 10.0.8 (`gates/kicad-cli/`): each
verdict names the release that ran, and a release the pack has no record of
still runs, and says so. A kicad-cli reads the files of its own release and older
ones: kicad-cli 9 skips a board or schematic KiCad 10 saved, naming the release,
so a KiCad 10 design needs KiCad 10's kicad-cli.

| Platform | How |
|---|---|
| Ubuntu / Debian | `sudo add-apt-repository ppa:kicad/kicad-10.0-releases && sudo apt install kicad` (KiCad 9: `ppa:kicad/kicad-9.0-releases`) |
| Fedora | `sudo dnf install kicad` |
| macOS | the installer from kicad.org; then `kicad-cli` is in `/Applications/KiCad/KiCad.app/Contents/MacOS/`, which must be put on PATH |
| Windows | the installer from kicad.org; `kicad-cli.exe` is in `C:\Program Files\KiCad\10.0\bin\` (Windows is untested with nopekit) |
| Any host with Docker, no root | the `kicad/kicad:10.0` image, through a wrapper named `kicad-cli` on PATH (below) |

The wrapper runs the image's kicad-cli with the project mounted at its own path,
so the absolute paths the gates pass resolve the same inside, and as your own
user, so the files it writes are yours. Leave the container's `HOME` as the image
ships it: its KiCad configuration, with the stock library tables, is there, and an
empty `HOME` makes every stock symbol a "library not in the current
configuration" finding. Mount every directory the gates name (the project, the
system temporary directory if the project's out directory is under it):

    #!/bin/sh
    exec docker run --rm --user "$(id -u):$(id -g)" \
      -v "$HOME/work:$HOME/work" -v /tmp:/tmp \
      -w "$PWD" kicad/kicad:10.0 kicad-cli "$@"

Check: the wrapper's `kicad-cli version --format plain` prints `10.0.8` or later.
A container start costs about 0.9 s a run. `pcb.antenna_keepout`'s refill needs
`pcbnew` in nopekit's own Python (below), which a wrapper does not give.

The full KiCad install is about 1-2 GB with its libraries. The libraries matter for
`pcb.pin_names` only at design time: the names are copied onto the board, and the
gate reads the board.

The baseline's generators (`selftest/assets/make_baseline.py`,
`make_power_board.py`, `make_radio_board.py`) also need KiCad's `pcbnew` Python
module (it ships with KiCad and runs under the system Python on Linux), and
KiCad 9's. `make_libraries.py` copies the stock entries the baseline was drawn
from. `make_kicad10.py` runs inside the `kicad/kicad:10.0` image, which has KiCad
10's `pcbnew` and kicad-cli; its docstring gives the command.

## ngspice (for `pcb.spice`)

| Platform | How |
|---|---|
| Ubuntu / Debian | `sudo apt install ngspice` |
| Fedora | `sudo dnf install ngspice` |
| macOS | `brew install ngspice` |
| Windows | the zip from ngspice.sourceforge.io; put `ngspice_con.exe`'s folder on PATH as `ngspice` |

Without root on Debian or Ubuntu: `apt-get download ngspice` and `dpkg -x` the
package into a directory of your own, then run its `usr/bin/ngspice` with
`SPICE_LIB_DIR` and `SPICE_SCRIPTS` pointing at its `usr/lib/.../ngspice` and
`usr/share/ngspice/scripts` (a two-line wrapper on PATH). Its `spinit` names the
code models by absolute path: edit those lines to the unpacked copy. Checked with
ngspice 42.

## CadQuery (for `pcb.enclosure_fit`)

`pip install cadquery` (2.8 checked; it brings OpenCascade as `OCP`). On a system
Python that refuses pip, `pip install --user --break-system-packages cadquery`, or
a virtual environment that nopekit runs from. Check: `python3 -c "import
cadquery"`. The fit also needs the parts' 3D models: KiCad's 3D library
(`kicad-packages3d` on Debian and Ubuntu, a separate download elsewhere), or the
models kept beside the board as `${KIPRJMOD}/...`.

**The 3D-model path variables.** A footprint names its model through the path
variable of the KiCad its library came from: `${KICAD6_3DMODEL_DIR}`,
`${KICAD7_3DMODEL_DIR}`, `${KICAD8_3DMODEL_DIR}`, `${KICAD9_3DMODEL_DIR}` or
`${KICAD10_3DMODEL_DIR}` (a board saved by KiCad 6 or 7 names
`${KICAD6_3DMODEL_DIR}/...wrl`). kicad-cli takes the value from the environment or
from KiCad's own configuration, and a host where KiCad was never opened has no
configuration: there the model is not found, and kicad-cli leaves the part out of
the export. So set, in the environment nopekit runs in, each variable your boards'
footprints name, to the 3D library's directory (`/usr/share/kicad/3dmodels` from
the Debian and Ubuntu package), for example
`export KICAD6_3DMODEL_DIR=/usr/share/kicad/3dmodels`. Keep the file the footprint
names and its `.step` beside it: the export reads the STEP of the same name in
place of a `.wrl` (`--subst-models`), and in the holdout run a part whose named
`.wrl` was missing was left out though its `.step` was there (reported there, not
measured here). Only `pcb.enclosure_fit` reads models; no other gate needs them.

## pcbnew (for `pcb.antenna_keepout`)

The keep-out gate refills the board's zones with KiCad's own filler before its
DRC, and asks KiCad which holes carry no copper, in a child process run by the
same Python as nopekit (`sys.executable`), so that Python must import `pcbnew`. Check: `python3 -c "import pcbnew;
print(pcbnew.Version())"` prints `9.0.x` or `10.0.x`.

The `pcbnew` that Python imports must read the board's format: pcbnew 9 does
not read a board KiCad 10 saved (measured: 9.0.9's refill raises an
AttributeError on one), so a KiCad 10 board needs pcbnew 10. The gate was not
yet run on a KiCad 10 board under pcbnew 10, so for now it skips a KiCad 10
board under any `pcbnew`, saying "this gate was checked through board format
20241229 (KiCad 9.0)" (PACK.md, "A KiCad 10 board in the fit and the keep-out").
`pcb.enclosure_fit` skips one too, for its STEP export.

| Platform | How |
|---|---|
| Ubuntu / Debian | the KiCad package installs `pcbnew` for the system `python3`: KiCad 10's from `ppa:kicad/kicad-10.0-releases` (the `kicad` package, as in the kicad-cli table), KiCad 9's from `ppa:kicad/kicad-9.0-releases`. Run nopekit with that Python, not a virtual environment that hides it |
| Fedora | the distribution's KiCad package installs `pcbnew` for the system `python3`, of the release the distribution ships |
| macOS | run nopekit with KiCad's bundled Python, `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3` |
| Windows | run nopekit with KiCad's bundled Python, in `C:\Program Files\KiCad\10.0\bin\` (KiCad 10) or `C:\Program Files\KiCad\9.0\bin\` (KiCad 9) (Windows is untested with nopekit) |
| Any host with Docker | the `kicad/kicad:10.0` image's `python3` (3.13) imports pcbnew 10.0.8 (checked), and nopekit needs only the standard library, so it can run there with the project mounted. A `kicad-cli` wrapper alone does not give `pcbnew`. This route is not yet measured on a gate |

The gate imports nothing from KiCad in nopekit's own process: availability looks
for the module without importing it, and only the child loads it.

## For `pcb.autoroute` (a proposer, optional)

- A Python that imports `pcbnew`: on Linux the distribution's KiCad package installs
  it for the system `python3`; on macOS and Windows use KiCad's bundled Python
  (`--kicad-python`).
- Java 21 or newer: any OpenJDK build, e.g. Temurin from adoptium.net. A JRE in a
  user directory works: point `JAVA` at its `bin/java`.
- Freerouting: the `freerouting-<version>.jar` from
  github.com/freerouting/freerouting/releases (2.2.4 checked). Point
  `FREEROUTING_JAR` at it.

None of the Java or Freerouting is needed by any gate.
