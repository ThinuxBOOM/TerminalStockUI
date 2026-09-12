"""Data-quality grades A/B/C/D (+F alias for unusable), per docs/DATA_QUALITY.md.

A: fresh, reconciled, complete. B: minor staleness or single-source.
C: stale / fallback / material field missing. D: unusable (blocked).
F is accepted anywhere a grade is carried and means the same as D.
"""

from __future__ import annotations


def grade_quality(
    *,
    delay_minutes: int = 15,
    age_minutes: float = 0.0,
    missing_fields: list[str] | None = None,
    fallback_used: bool = False,
    reconciled: bool = False,
    diverged: bool = False,
    invalid: bool = False,
) -> tuple[str, list[str]]:
    """Return (grade, reasons). Deterministic: identical inputs -> identical grade."""
    missing = list(missing_fields or [])
    material_missing = [f for f in missing if f in ("price", "close")]
    reasons: list[str] = []

    if invalid:
        reasons.append("validation-failed")
        return "D", reasons

    expected = max(int(delay_minutes), 1)
    stale = age_minutes > 2 * expected
    minor_stale = (not stale) and age_minutes > expected

    if material_missing:
        reasons.append(f"missing-material:{','.join(material_missing)}")
        return "C", reasons
    if fallback_used:
        reasons.append("fallback-used")
        grade = "C"
    elif stale:
        reasons.append(f"stale:{age_minutes:.0f}m>{2 * expected}m")
        grade = "C"
    elif diverged:
        reasons.append("reconciliation-diverged")
        grade = "C"
    elif minor_stale:
        reasons.append(f"minor-staleness:{age_minutes:.0f}m")
        grade = "B"
    elif missing:
        reasons.append(f"missing:{','.join(missing)}")
        grade = "B"
    elif not reconciled:
        reasons.append("single-source")
        grade = "B"
    else:
        grade = "A"

    if diverged and "reconciliation-diverged" not in reasons:
        reasons.append("reconciliation-diverged")
        grade = "C" if grade in ("A", "B") else grade
    return grade, reasons
