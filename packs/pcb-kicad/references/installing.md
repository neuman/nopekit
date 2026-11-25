# Installing the tools

The tier-0 gates (`pcb.board_readable`, `pcb.pin_names`, `pcb.net_width`) need
nothing. `pcb.drc`, `pcb.unconnected`, `pcb.erc`, `pcb.parity` and `pcb.fab_rules`
need `kicad-cli` from KiCad 8 or 9 on PATH; `pcb.spice` needs `ngspice`;
`pcb.enclosure_fit` needs `kicad-cli` and the Python module `cadquery`;
`pcb.antenna_keepout` needs `kicad-cli` and KiCad's own Python module `pcbnew`,
importable by the Python that runs nopekit. Without its tool a gate reads
Skipped, never passed.

## kicad-cli

Check: `kicad-cli version` prints `9.0.x`.

| Platform | How |
|---|---|
| Ubuntu / Debian | `sudo add-apt-repository ppa:kicad/kicad-9.0-releases && sudo apt install kicad` |
| Fedora | `sudo dnf install kicad` |
| macOS | the installer from kicad.org; then `kicad-cli` is in `/Applications/KiCad/KiCad.app/Contents/MacOS/`, which must be put on PATH |
| Windows | the installer from kicad.org; `kicad-cli.exe` is in `C:\Program Files\KiCad\9.0\bin\` (Windows is untested with nopekit) |

The full KiCad install is about 1-2 GB with its libraries. The libraries matter for
`pcb.pin_names` only at design time: the names are copied onto the board, and the
gate reads the board.

The baseline's generators (`selftest/assets/make_baseline.py`,
`make_power_board.py`, `make_radio_board.py`) also need KiCad's `pcbnew` Python
module (it ships with KiCad and runs under the system Python on Linux).

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

## pcbnew (for `pcb.antenna_keepout`)

The keep-out gate refills the board's zones with KiCad's own filler before its
DRC, in a child process run by the same Python as nopekit (`sys.executable`), so
that Python must import `pcbnew`. Check: `python3 -c "import pcbnew;
print(pcbnew.Version())"` prints `9.0.x`.

| Platform | How |
|---|---|
| Ubuntu / Debian, Fedora | the distribution's KiCad package installs `pcbnew` for the system `python3`: run nopekit with that Python, not a virtual environment that hides it |
| macOS | run nopekit with KiCad's bundled Python, `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3` |
| Windows | run nopekit with KiCad's bundled Python, in `C:\Program Files\KiCad\9.0\bin\` (Windows is untested with nopekit) |

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
