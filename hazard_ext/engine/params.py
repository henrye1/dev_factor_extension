"""Scenario parameters – the inputs of the workbook's Config sheet."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    event_type: str = Field("Lifetime", max_length=100, description="EventType block of the debug file")
    rate: Optional[float] = Field(None, gt=-1, le=10, description="Discount rate p.a.; blank = implied by the file")
    target_ts: int = Field(300, ge=1, le=2000, description="LGD is produced for TermStep 1..target")
    max_bucket: int = Field(420, ge=1, le=2000, description="Buckets are extended to this index")
    min_exposure_mode: Literal["abs", "pct"] = Field("abs", description="abs = Rand, pct = % of TermStep 1 opening exposure")
    min_exposure: float = Field(100_000_000.0, ge=0, le=1e15, description="Credibility cut")
    window: int = Field(12, ge=1, le=2000, description="Last W credible buckets anchor the tail level")
    fit_start: int = Field(24, ge=1, le=2000, description="Start of the regression window for lambda / gamma")
    ref_ts: int = Field(1, ge=1, le=2000, description="TermStep row used to fit lambda / gamma")
    method: Literal[1, 2, 3] = Field(3, description="1 exponential, 2 power law, 3 reference curve shape")
    client_cohort: Optional[str] = Field(None, max_length=100, description="Reference curve label; blank = the zip's Category1")
    horizon: int = Field(12, ge=1, le=2000, description="Short horizon (months)")
    horizon2: int = Field(120, ge=1, le=2000, description="Valuation horizon (months)")
    lambda_override: Optional[float] = Field(None, ge=-1, le=50, description="Exponential decay per bucket; blank = fitted")
    gamma_override: Optional[float] = Field(None, ge=-10, le=100, description="Power-law exponent; blank = fitted")
    floor: float = Field(0.0, ge=0, le=1, description="Minimum RecoveryPct on the extended tail")
    base_ts: int = Field(1, ge=1, le=2000, description="Row rolled forward beyond LastTS")
    last_ts: Optional[int] = Field(None, ge=0, le=2000, description="Last TermStep used as-is; blank = last observed")

    @model_validator(mode="after")
    def _check(self):
        if self.min_exposure_mode == "pct" and self.min_exposure > 100:
            raise ValueError("min_exposure as a percentage cannot exceed 100")
        if isinstance(self.client_cohort, str) and not self.client_cohort.strip():
            self.client_cohort = None
        return self


DEFAULTS = Params().model_dump()
FIELDS = list(Params.model_fields.keys())


def clean_overrides(override: dict | None) -> dict:
    """Check that a partial override names only known parameters.

    The values are validated by ``merge_params`` against the scenario they sit on: an override
    such as ``{"min_exposure_mode": "pct"}`` is only valid or invalid in that combination.
    """
    override = dict(override or {})
    unknown = [k for k in override if k not in Params.model_fields]
    if unknown:
        raise ValueError("Unknown parameter(s): " + ", ".join(sorted(unknown)))
    return override


def merge_params(base: dict | None, override: dict | None = None) -> Params:
    """Scenario parameters with a per-zip override laid on top. Override keys win."""
    merged = {**DEFAULTS, **(base or {}), **(override or {})}
    return Params(**merged)
