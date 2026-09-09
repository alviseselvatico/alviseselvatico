"""Rights gate. No downstream module may bypass it (ARCHITECTURE §3)."""

from vme.rights.gate import Action, GateDecision, RightsBlockedError, check, require

__all__ = ["Action", "GateDecision", "RightsBlockedError", "check", "require"]
