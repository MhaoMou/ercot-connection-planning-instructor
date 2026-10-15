"""Source-checkout shim that exposes the canonical ``src`` package API."""

from pathlib import Path
import runpy


_SOURCE_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "ercot_game"
__path__ = [str(_SOURCE_PACKAGE)]
globals().update(
    runpy.run_path(
        str(_SOURCE_PACKAGE / "__init__.py"),
        init_globals={"__name__": __name__, "__package__": __name__, "__path__": __path__},
        run_name=__name__,
    )
)
