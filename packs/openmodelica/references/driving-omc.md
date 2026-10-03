# Driving omc from a script

## Why a `.mos` file and a CLI, and not OMPython

OpenModelica exposes the same scripting API three ways: a `.mos` script handed to
the `omc` binary, an interactive `omc` REPL, and a ZMQ session driven from Python
by OMPython (`OMCSessionZMQ`). All three send the same expressions to the same
compiler. This pack uses the first, and the choice has a reason worth recording
(method rule 3 applies to tooling):

| | `omc script.mos` | `OMCSessionZMQ` |
|---|---|---|
| Needs | `omc` on PATH | `omc`, a matching OMPython, a live server process, a ZMQ port |
| Third-party imports at module scope | none | `OMPython`, `pyzmq` |
| Failure mode when the tool is missing | `shutil.which` returns None → clean SKIP | ImportError, or a hang waiting on a socket |
| Reproducible by a reader | `omc out/check.mos` — one command | "start a session and send these expressions" |
| Evidence | the script and the log, both on disk | a transcript, if you kept one |
| Cost of a version bump | none | the client must match the server |

The last two rows are the argument. A tier-2 verdict a reader cannot reproduce by
running one command is a verdict they have to take on trust, and this pack's
whole claim is that nobody should have to. The reference implementation this
knowledge came from drove omc both ways; only the script form survived being run
on a machine that was not the author's.

ZMQ wins on one thing — holding a loaded model across many calls, which matters
if you are sweeping a parameter hundreds of times. If you need that, write it as
a generator that emits one `.mos` doing the whole sweep. It is still one command.

## The scripting API, in the order a gate uses it

```modelica
loadModel(Modelica);                   // a library, by class name, unquoted
loadFile("/abs/path/MyPackage.mo");    // a file, by path, quoted. Returns Boolean
getErrorString();                      // AND CLEARS IT. Call after every step
checkModel(MyPackage.Run);             // -> String, includes the balance line
buildModel(MyPackage.Run);             // -> String[2]: {executable, init xml}
simulate(MyPackage.Run, stopTime=25000, numberOfIntervals=25,
         tolerance=1e-6, outputFormat="csv");   // -> a SimulationResult record
```

Four things about this API that cost an afternoon each if nobody tells you:

1. **`getErrorString()` clears the buffer.** Call it after every step, or step
   three's errors get attributed to step five. Call it twice in a row and the
   second call returns `""` — which reads exactly like success.
2. **Class names are unquoted; file paths are quoted.** `checkModel("Foo")` is
   not `checkModel(Foo)`. The first is a string and does nothing useful.
3. **`loadFile` returning `true` means the file PARSED**, not that the classes in
   it are usable. A file whose `within` clause disagrees with its directory
   loads happily and then cannot be found by name.
4. **Statement echo vs `print`.** A statement ending in `;` echoes its result;
   an assignment `x := expr;` does not. `print(s)` writes a String verbatim,
   without the escaping the echo applies. These gates use `print` where the value
   is a String (so error text arrives unescaped and greppable) and the echo where
   it is a record or an array (so the record's own stable text format can be
   parsed).

Load order matters: **libraries before sources**. A source that `extends` an MSL
class fails to instantiate when the library is not in the symbol table yet, and
the resulting error names *your* class, sending you to debug the wrong file.

## What these gates actually generate

`modelica.checks` writes this to `<out_dir>/omc/check.mos` and runs
`omc check.mos` from `<out_dir>/omc`:

```modelica
print("@@ATOMPIPE:libraries\n");
print("loadModel(Modelica) = " + String(loadModel(Modelica)) + "\n");
print("@@ATOMPIPE:load\n");
print("loadFile(ThermalTank.mo) = " + String(loadFile("/abs/<out_dir>/omc/.sources/check/0/model/ThermalTank.mo")) + "\n");
print("@@ATOMPIPE:loaderr\n");
print(getErrorString() + "\n");
print("@@ATOMPIPE:check\n");
print(checkModel(ThermalTank.TankRun) + "\n");
print("@@ATOMPIPE:checkerr\n");
print(getErrorString() + "\n");
print("@@ATOMPIPE:end\n");
```

The `@@ATOMPIPE:` markers are how the output is segmented, and they are chosen to
be something no Modelica model, error message or file path will contain — a
segmenter that split on "Error" would eat the first line of every real error.
`modelica.compiles` replaces the check block with `built := buildModel(C);
print(built[1] + "\n");`, and `modelica.simulates` with an echoed
`simulate(...)`.

Both the script and a full log (script, return code, duration, stdout, stderr)
are written to `<out_dir>/omc/` and cited in the verdict's `evidence`. To
reproduce any tier-2 verdict: `cd` to that directory and run `omc check.mos`.

The script never loads your files where they live. Each run first copies every
`modelica_sources` entry under `.sources/<script>/<n>/` beside the script — a
directory as its whole tree, so a package keeps its name, its `package.order`
and its `Resources/`; dot-directories left out — and `loadFile` names the copy.
omc runs from that directory, so an omc behind a wrapper sees everything it is
asked to load as long as the wrapper mounts the directory omc runs in, which it
must anyway. The log lists each copy beside its original, and keeps omc's output
as printed; the verdicts put every copy's path back to yours, so an error points
at the file you edit. Edit the originals: the copies are rebuilt on every run,
which is also why a file you deleted is never loaded from an old copy.
`omc check.mos` by hand reproduces the copies the gate loaded, not your edits
since.

Before every run the gate deletes each `*.o` in that directory. omc's generated
makefile rebuilds the main object `<Class>.o` only when `<Class>.c` is newer, and
the main file carries the model GUID, which changes on every translation. When
the clock steps backwards between two builds of one class (seen on WSL2),
make keeps the old object, and the executable then refuses its own
`_init.xml`: "the GUID ... from input data file ... does not match the GUID
compiled in the model". If you reproduce a run by hand in a directory that
already holds a build, run `rm -f *.o` first.

## Parsing what comes back

**`checkModel`** returns a String like:

```
Check of ThermalTank.TankRun completed successfully.
Class ThermalTank.TankRun has 3 equation(s) and 3 variable(s).
1 of these are trivial equation(s).
```

The gate greps `has (\d+) equation\(s\) and (\d+) variable\(s\)`. Unequal counts
are the failure. The wording of the first and third lines has changed between
OpenModelica releases; the balance line has not.

**`buildModel`** echoes a two-element array: `{"/path/Run", "/path/Run_init.xml"}`
on success, `{"",""}` on failure. The gate additionally checks the named
executable is on disk, because a build can name a file it did not finish writing.

**`simulate`** echoes a record whose text form is stable across releases:

```
record SimulationResult
    resultFile = "/path/ThermalTank.TankRun_res.csv",
    simulationOptions = "startTime = 0.0, stopTime = 25000.0, ...",
    messages = "",
    timeFrontend = 0.21, timeBackend = 0.08, ... timeTotal = 2.31
end SimulationResult;
```

The gate reads `resultFile` and `messages` out of it, then **reads the end time
out of the result file itself** rather than trusting `simulationOptions`, which
echoes what was *requested*. An assert that fired leaves `messages` non-empty,
the process exit code non-zero, and a perfectly readable result file that stops
early — and only the third of those is the one a careless pipeline checks.

## Useful flags and options, and when they are worth it

```modelica
setCommandLineOptions("-d=initialization");     // dump the initialisation system
setCommandLineOptions("--unitChecking");        // dimensional analysis of equations
setCommandLineOptions("-d=bltdump");            // the matching/tearing result
simulate(C, simflags="-lv=LOG_STATS,LOG_INIT"); // runtime statistics and init log
simulate(C, method="dassl");                    // the default; "euler"/"rungekutta" exist
simulate(C, variableFilter="tank\\..*");        // what lands in the result file
```

`--unitChecking` is the one to try early and expect to lose. It performs the
dimensional analysis that `modelica.source_hygiene` deliberately does NOT attempt,
and most real models produce hundreds of warnings the first time it is switched
on. That output is a finding, not a nuisance.

`variableFilter` is a regex over variable names and it is a foot-gun: it silently
excludes everything it does not match, and a result file missing a variable is
valid, readable and useless. `modelica_required_variables` exists because of it.

## Exit codes

`omc` exits 0 for a script that ran, whatever the script's expressions said —
`checkModel` of a broken model is a successful `omc` invocation. Non-zero
generally means the *script* failed to run (a syntax error in the `.mos`, a
missing file argument) or a simulation executable exited non-zero. So: a
non-zero exit is evidence, a zero exit is not. Every gate here reads the content,
not the code.

## Timeouts

Every invocation runs under a timeout (`modelica_omc_timeout_s`,
`modelica_simulate_timeout_s`). A timeout is reported as a **SKIP, never a FAIL**:
a model that did not finish inside the budget has not been shown to be wrong, and
filing it as a failure sends somebody to edit equations when the honest fix is a
bigger budget or a smaller model. The skip reason names the budget and the key
that changes it.
