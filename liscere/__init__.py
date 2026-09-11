"""Liscere: a passive observer of industrial control traffic.

The engine lives in ``otlab_core`` (protocol-neutral core in ``otlab_core.engine``, passive
protocol extractors in ``otlab_core.extractors``). This package holds the command-line programs:

- ``liscere-observe`` (``liscere.observe``): probe the wire, discover the state signal, calibrate,
  learn the operational grammar, judge every supervisory write;
- ``liscere-probe``   (``liscere.probe``):   report which protocol silos are on an interface;
- ``liscere-ui``      (``liscere.ui``):      terminal view of the observer's JSON Lines stream.

The version is the single value declared in ``pyproject.toml``; it is read from the installed
package metadata so nothing else has to be kept in step.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("liscere")
except PackageNotFoundError:  # running from a checkout that has not been installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
