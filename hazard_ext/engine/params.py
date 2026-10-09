"""Scenario parameters – the inputs of the workbook's Config sheet."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Parameters that older saved scenarios, overrides and results may still carry. They are
# dropped before validation so that stored data keeps loading after a parameter is retired.
RETIRED = {"client_cohort"}        # the reference-curve shape, replaced by log-normal (9 Oct 2026)

_MONTH = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    event_type: str = Field("Lifetime", max_length=100, description="EventType block of the debug file")
    rate: Optional[float] = Field(None, gt=-1, le=10, description="Discount rate p.a.; blank = implied by the file")
    target_ts: int = Field(300, ge=1, le=2000, description="LGD is produced for TermStep 1..target")
    max_bucket: int = Field(420, ge=1, le=2000, description="Buckets are extended to this index")
    min_exposure_mode: Literal["abs", "pct"] = Field("abs", description="abs = Rand, pct = % of TermStep 1 opening exposure")
    min_exposure: float = Field(100_000_000.0, ge=0, le=1e15, description="Credibility cut")
    window: int = Field(12, ge=1, le=2000, description="Last W credible buckets anchor the tail level")
    fit_start: int = Field(24, ge=1, le=2000, description="Start of the regression window for the decay parameters")
    ref_ts: int = Field(1, ge=1, le=2000, description="TermStep row used to fit the decay parameters")
    method: Literal[1, 2, 3] = Field(1, description="1 exponential, 2 power law, 3 log-normal")
    horizon: int = Field(12, ge=1, le=2000, description="Short horizon (months)")
    horizon2: int = Field(120, ge=1, le=2000, description="Valuation horizon (months)")
    lambda_override: Optional[float] = Field(None, ge=-1, le=50, description="Exponential decay per bucket; blank = fitted")
    gamma_override: Optional[float] = Field(None, ge=-10, le=100, description="Power-law exponent; blank = fitted")
    mu_override: Optional[float] = Field(None, ge=-10, le=20, description="Log-normal μ (on ln b); blank = fitted")
    sigma_override: Optional[float] = Field(None, gt=0, le=20, description="Log-normal σ; blank = fitted")
    floor: float = Field(0.0, ge=0, le=1, description="Minimum RecoveryPct on the extended tail")
    base_ts: int = Field(1, ge=1, le=2000, description="Row rolled forward beyond LastTS")
    last_ts: Optional[int] = Field(None, ge=0, le=2000, description="Last TermStep used as-is; blank = last observed")
    vintage_start: Optional[str] = Field(None, max_length=10,
                                         description="Include default vintages from this month (YYYY-MM); blank = all vintages")
    vintage_years: Optional[int] = Field(None, ge=1, le=50,
                                         description="Include vintages within the last N years of the zip's latest vintage; blank = all")

    @model_validator(mode="after")
    def _check(self):
        if self.min_exposure_mode == "pct" and self.min_exposure > 100:
            raise ValueError("min_exposure as a percentage cannot exceed 100")
        if isinstance(self.vintage_start, str):
            self.vintage_start = normalise_month(self.vintage_start)
        if self.vintage_start is not None and self.vintage_years is not None:
            raise ValueError("Choose either a vintage start month or a number of years, not both")
        return self


def normalise_month(text: str) -> str | None:
    """'2016-08' or '2016-08-31' -> '2016-08'. Blank -> None."""
    text = text.strip()
    if not text:
        return None
    m = _MONTH.match(text)
    if not m:
        raise ValueError("vintage_start must be a month written as YYYY-MM")
    year, month = int(m.group(1)), int(m.group(2))
    if not (1900 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("vintage_start must be a month between 1900-01 and 2100-12")
    return f"{year:04d}-{month:02d}"


DEFAULTS = Params().model_dump()
FIELDS = list(Params.model_fields.keys())


def drop_retired(params: dict | None) -> dict:
    """A copy of stored parameters without the keys that no longer exist."""
    return {k: v for k, v in (params or {}).items() if k not in RETIRED}


def load_params(stored: dict | None) -> Params:
    """Validate a full parameter set as stored on a result, tolerating retired keys."""
    return Params(**{**DEFAULTS, **drop_retired(stored)})


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
    merged = {**DEFAULTS, **drop_retired(base), **drop_retired(override)}
    return Params(**merged)
