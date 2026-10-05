# SPDX-License-Identifier: Apache-2.0
"""nopekit — the parametric-design spine: claims -> gates -> packs -> readiness.

Importing this package is deliberately almost free. `import nopekit` binds a
version string and a module-level `__getattr__`, and nothing else: no models, no
store, no argparse. A pack's gate module, a test, or a tab-completion hook that
touches `nopekit` should not pay for the whole spine, and an expensive
top-level import is how a "runs in seconds" inner loop (rule 10) quietly stops
running in seconds.

Everything public in `nopekit.models` is reachable from here on first use::

    import nopekit
    c = nopekit.Claim(id="C1", statement="floats at full load")
    nopekit.models.slugify("Hull beam")        # the module itself, too

That resolution is lazy (PEP 562) and derived from `models.__all__` rather than
copied into a list here — restating the contract's export list in a second file
is precisely the duplication the method exists to eliminate, and the copy is
always the one that goes stale.
"""
from __future__ import annotations

__version__ = "0.1.0"

#: submodules reachable as attributes of the package, imported on first touch.
#: The spine's own modules import each other directly (`from . import store`);
#: this map is for callers and for `nopekit.models.X` style access.
_SUBMODULES = frozenset({
    "models", "util", "store", "modelio", "gates", "claims",
    "artifacts", "packs", "decisions", "report", "site", "cli",
})

#: names that live in a module other than `models`. NopekitError is here
#: because it is the one exception a caller ever catches, and making them
#: remember which file it lives in is a papercut with no upside.
_ALIASES = {"NopekitError": "util"}


def __getattr__(name: str):          # deliberately unannotated: see below
    """Resolve `nopekit.<name>` lazily (PEP 562).

    No return annotation because spelling it `-> Any` would mean importing
    `typing` at package import time, measured here at 4.6 ms and 14 extra
    modules — roughly 80% of the entire cost of `import nopekit`. A spine whose
    promise is a tier-0 loop that "runs in seconds" does not spend that on one
    type name; a checker infers `Any` from the `getattr` below anyway.

    Order matters: submodules first, then explicit aliases, then anything
    exported by `models`. A name that is not in `models.__all__` is NOT
    reachable from here even if it exists in the module — the contract's
    `__all__` is the public surface, and a private helper that leaks into the
    package namespace becomes something someone depends on by accident.
    """
    if name in _SUBMODULES:
        from importlib import import_module
        return import_module(f".{name}", __name__)

    if name in _ALIASES:
        from importlib import import_module
        return getattr(import_module(f".{_ALIASES[name]}", __name__), name)

    if name == "__all__":
        # Only consulted by `from nopekit import *`. Derived, never duplicated.
        from importlib import import_module
        models = import_module(".models", __name__)
        return ["__version__", "models", "NopekitError", *models.__all__]

    if not name.startswith("_"):
        from importlib import import_module
        models = import_module(".models", __name__)
        if name in getattr(models, "__all__", ()):
            return getattr(models, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Make the lazy names discoverable to tab-completion and `dir()`."""
    from importlib import import_module
    models = import_module(".models", __name__)
    return sorted({"__version__", *_SUBMODULES, *_ALIASES, *models.__all__})
