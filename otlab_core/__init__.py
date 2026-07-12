"""OT Lab protocol-abstracted passive observer core.

Phase 1 of the protocol-engine work. This package introduces:

- ``contract``   : the ``NormalizedEvent`` data contract (protocol-neutral event shape);
- ``extractors`` : the ``ProtocolExtractor`` interface + ``ModbusExtractor`` (passive dissection);
- ``engine``     : the protocol-neutral learned core (PhaseTracker, grammar learner, graded
                   evaluator) brought in from LTR-2026-03.

Nothing here changes existing OT Lab behaviour on its own: the capture runtime serialises
``NormalizedEvent`` back to the exact legacy JSON, and the learned engine is provided as a
module + thin adapter that is NOT wired into the live passive path in this phase.
"""

__all__ = ["contract", "extractors", "engine"]
