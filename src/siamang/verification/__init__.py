"""Verification engine (Phase 26): a pass comes from real execution in the sandbox, not from a model's claim."""

from siamang.verification.report import CheckOutcome, VerificationReport, VerificationStatus
from siamang.verification.service import Check, VerificationService, parse_command

__all__ = ["Check", "CheckOutcome", "VerificationReport", "VerificationService", "VerificationStatus",
           "parse_command"]
