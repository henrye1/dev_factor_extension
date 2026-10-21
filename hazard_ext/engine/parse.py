"""Read a risk-suite debug zip into a compact, serialisable RecoveryData object.

Three members of the zip are used: ``lgd_recovery.csv`` (the recovery triangle over all default
vintages), ``runoff_triangle.csv`` (exposure by default vintage and month, from which the
triangles are built for a chosen set of vintages, and which fixes the report date) and ``debug.json``.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np
import pandas as pd

from ._util import max_abs, weighted_mean

COLUMNS = [
    "EventType", "TermStep", "TermDays", "ExposureBucket", "BucketIndex",
    "PrevColSum", "ThisColSum", "RecoveryPct", "DiscountIndex",
    "DiscountFactor", "Contribution", "CumulativeSumPV", "LGD",
]
NUM_COLUMNS = COLUMNS[1:]
_IX = {name: i for i, name in enumerate(NUM_COLUMNS)}

RUNOFF_COLUMNS = ["EventType", "CohortDate", "Bucket", "ExposureAmount"]

MAX_TERMSTEP = 2000                 # triangles are (n+1) x (n+1); this caps them at 32 MB each
MAX_CSV_BYTES = 600 * 1024 * 1024   # uncompressed lgd_recovery.csv (the largest seen is 31 MB)
MAX_RUNOFF_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_COHORTS = 5000

SELF_CHECK_ATOL = 1e-9              # RecoveryPct and LGD, absolute
SELF_CHECK_RTOL = 1e-9              # ExposureBucket, relative


class ParseError(ValueError):
    """The uploaded file is not a usable debug zip."""


class VintageError(ValueError):
    """A vintage filter was asked for but cannot be applied to this dataset."""


@dataclass
class Triangles:
    """Observed triangles for one EventType. Arrays are 1-indexed: [ts, b]."""

    n: int                    # highest TermStep / bucket in the file
    R: np.ndarray             # RecoveryPct, shape (n+1, n+1), 0 where absent
    E: np.ndarray             # ExposureBucket, same shape
    lgd_file: np.ndarray      # LGD in the file at its last observed bucket, per ts (nan if absent)
    lgd_unfloored: np.ndarray # 1 - CumulativeSumPV at the same row: the file's LGD before the
                              # risk suite floors it at the previous TermStep's LGD
    last_obs_file: int        # largest BucketIndex with exposure > 0 in the file
    implied_rate: float       # annual rate implied by the file's DiscountFactor


@dataclass
class RunoffBlock:
    """Exposure by default vintage and month since default, for one EventType."""

    cohort: np.ndarray        # int32[C], the vintage as yyyymmdd, ascending
    X: np.ndarray             # float64[C, K], ExposureAmount at bucket k = 0 .. K-1 (0 where no row)
    obs: np.ndarray           # bool[C, K], True where the runoff file has a row for (c, k)

    @property
    def months(self) -> np.ndarray:
        return self.cohort // 100                     # yyyymm


def month_text(yyyymm: int) -> str:
    return f"{yyyymm // 100:04d}-{yyyymm % 100:02d}"


def month_int(text: str) -> int:
    return int(text[:4]) * 100 + int(text[5:7])


def _triangles_from_rows(a: np.ndarray, implied_rate: float) -> Triangles:
    """Build the observed triangles from rows in the 12-column numeric layout of lgd_recovery."""
    ts = a[:, _IX["TermStep"]].astype(int)
    b = a[:, _IX["BucketIndex"]].astype(int)
    n = int(max(ts.max(), b.max()))
    R = np.zeros((n + 1, n + 1))
    E = np.zeros((n + 1, n + 1))
    keep = b >= ts                       # the workbook shows 0 where b < ts
    np.add.at(R, (ts[keep], b[keep]), a[keep, _IX["RecoveryPct"]])
    np.add.at(E, (ts[keep], b[keep]), a[keep, _IX["ExposureBucket"]])

    expo = a[:, _IX["ExposureBucket"]]
    last_obs_file = int(b[expo > 0].max()) if (expo > 0).any() else 0
    lgd_file = np.full(n + 1, np.nan)
    lgd_unfloored = np.full(n + 1, np.nan)
    at_last = np.nonzero(b == last_obs_file)[0][::-1]
    # reversed so the first matching row per TermStep wins, as INDEX/MATCH does
    lgd_file[ts[at_last]] = a[at_last, _IX["LGD"]]
    lgd_unfloored[ts[at_last]] = 1.0 - a[at_last, _IX["CumulativeSumPV"]]
    return Triangles(n=n, R=R, E=E, lgd_file=lgd_file, lgd_unfloored=lgd_unfloored,
                     last_obs_file=last_obs_file, implied_rate=implied_rate)


def replica_lgd(tri: Triangles) -> np.ndarray:
    """1 − PV of the observed RecoveryPct per TermStep, discounted as the file does
    (DiscountIndex = b − ts + 1): the LGD before any extension. Index 0 is nan."""
    n = tri.n
    v = (1.0 + tri.implied_rate) ** (-1.0 / 12.0)
    b = np.arange(n + 1, dtype=float)
    vb = v ** b
    vb[0] = 0.0
    disc = v ** (b - 1.0)                       # v^(ts-1), same index as ts
    pv = (tri.R * vb[None, :]).sum(axis=1) / disc
    out = 1.0 - pv
    out[0] = np.nan
    return out


def _vintage_sums(block: RunoffBlock, keep: np.ndarray):
    """ExposureBucket, PrevColSum and ThisColSum by [ts, b] for the kept vintages, as the risk suite
    builds lgd_recovery: for TermStep ts and bucket b >= ts, over the kept vintages that still hold a
    balance at bucket ts−1 and are observed at bucket b, the sums of their balances at ts−1, b−1 and b.
    Zero below the diagonal and in row / column 0."""
    C, K = block.X.shape
    n = K                                            # TermSteps 1..B+1, B the highest runoff bucket
    X = np.zeros((C, n + 1))
    X[:, :K] = block.X
    O = np.zeros((C, n + 1))
    O[:, :K] = block.obs
    P = ((X > 0) & np.asarray(keep, dtype=bool)[:, None]).astype(float)[:, :n]   # counts at ts = j + 1
    prev_b = np.zeros_like(X)
    prev_b[:, 1:] = X[:, :-1] * O[:, 1:]
    upper = np.triu(np.ones((n + 1, n + 1), dtype=bool))
    upper[0, :] = upper[:, 0] = False
    sums = []
    for M in ((X[:, :n] * P).T @ O, P.T @ prev_b, P.T @ (X * O)):
        out = np.zeros((n + 1, n + 1))
        out[1:] = M
        sums.append(np.where(upper, out, 0.0))
    return n, upper, sums[0], sums[1], sums[2]


def _discounting(R: np.ndarray, upper: np.ndarray, rate: float):
    """DiscountFactor (1 + r)^(−(b − ts + 1)/12) and CumulativeSumPV along each TermStep row."""
    ts = np.arange(R.shape[0])[:, None]
    b = np.arange(R.shape[1])[None, :]
    df = np.where(upper, (1.0 + rate) ** (-(b - ts + 1.0) / 12.0), 0.0)
    return df, np.cumsum(R * df, axis=1)


def _earlier_lgd(cum: np.ndarray, n: int) -> np.ndarray:
    """Per ts, the largest final LGD of the earlier TermSteps: the risk suite floors each LGD at it."""
    final = 1.0 - cum[:, n]
    return np.r_[-np.inf, -np.inf, np.maximum.accumulate(final[1:])[:-1]]


def vintage_triangles(block: RunoffBlock, keep: np.ndarray, rate: float) -> Triangles:
    """The observed triangles for the vintages flagged in ``keep``, built from the runoff data:
    RecoveryPct = (PrevColSum − ThisColSum) ÷ ExposureBucket (0 when there is no exposure). The LGD is
    1 − CumulativeSumPV, floored at the earlier TermSteps' LGD, read at the last observed bucket, as
    the file's LGD column is."""
    n, upper, E, prev, this = _vintage_sums(block, keep)
    with np.errstate(divide="ignore", invalid="ignore"):
        R = np.where(upper & (E > 0), (prev - this) / E, 0.0)
    seen = np.nonzero((E > 0).any(axis=0))[0]
    last_obs = int(seen.max()) if seen.size else 0
    _, cum = _discounting(R, upper, rate)
    rows = np.arange(1, min(last_obs, n) + 1)
    lgd_unf = np.full(n + 1, np.nan)
    lgd_unf[rows] = 1.0 - cum[rows, last_obs]
    lgd_file = np.full(n + 1, np.nan)
    lgd_file[rows] = np.maximum(lgd_unf[rows], _earlier_lgd(cum, n)[rows])
    return Triangles(n=n, R=R, E=E, lgd_file=lgd_file, lgd_unfloored=lgd_unf, last_obs_file=last_obs,
                     implied_rate=rate)


def vintage_rows(block: RunoffBlock, keep: np.ndarray, rate: float) -> np.ndarray:
    """The lgd_recovery rows (the 12 numeric columns, TermStep .. LGD) for the kept vintages, for the
    formula workbook's Raw_Debug sheet: one row per TermStep ts and bucket b = ts..B+1."""
    n, upper, E, prev, this = _vintage_sums(block, keep)
    with np.errstate(divide="ignore", invalid="ignore"):
        R = np.where(upper & (E > 0), (prev - this) / E, 0.0)
    df, cum = _discounting(R, upper, rate)
    lgd = np.maximum(1.0 - cum, _earlier_lgd(cum, n)[:, None])
    ts, b = np.nonzero(upper)                         # row-major: by TermStep, then bucket
    out = np.empty((len(ts), len(NUM_COLUMNS)))
    for name, col in (("TermStep", ts), ("TermDays", (ts - 1) * 30), ("ExposureBucket", E[ts, b]),
                      ("BucketIndex", b), ("PrevColSum", prev[ts, b]), ("ThisColSum", this[ts, b]),
                      ("RecoveryPct", R[ts, b]), ("DiscountIndex", b - ts + 1), ("DiscountFactor", df[ts, b]),
                      ("Contribution", (R * df)[ts, b]), ("CumulativeSumPV", cum[ts, b]), ("LGD", lgd[ts, b])):
        out[:, _IX[name]] = col
    return out


@dataclass
class RecoveryData:
    category: str
    meta: dict
    event_types: list[str]
    raw: dict[str, np.ndarray]            # event type -> (rows, 12) numeric columns in file order
    identical_events: bool = True
    runoff: dict[str, RunoffBlock] = field(default_factory=dict)   # event type -> block (may be empty)
    identical_runoff: bool = True
    runoff_status: dict = field(default_factory=dict)              # {"ok": bool, "reason": str}
    _tri_cache: dict = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------ access
    def block(self, event: str) -> np.ndarray:
        if event not in self.raw:
            raise ParseError(
                f"EventType '{event}' is not in this file (available: {', '.join(self.event_types)})")
        return self.raw[event]

    @property
    def has_runoff(self) -> bool:
        return bool(self.runoff)

    @property
    def vintage_filter_available(self) -> bool:
        return self.has_runoff and bool(self.runoff_status.get("ok"))

    def runoff_block(self, event: str) -> RunoffBlock:
        """The runoff block for an EventType, when the vintage filter may be used on it."""
        self.block(event)
        if not self.has_runoff:
            raise VintageError("This zip was stored without its runoff_triangle data. Upload the zip again "
                               "to enable the vintage filter")
        if not self.runoff_status.get("ok"):
            raise VintageError("The vintage filter is not available for this zip: its runoff_triangle "
                               "does not reproduce lgd_recovery (" + str(self.runoff_status.get("reason", "")) + ")")
        if event not in self.runoff:
            raise VintageError(f"runoff_triangle has no block for EventType '{event}'")
        return self.runoff[event]

    def vintage_summary(self, event: str) -> dict:
        """First and last vintage (YYYY-MM) and the cohort count, or Nones without runoff data."""
        if not self.has_runoff or event not in self.runoff:
            return {"vintage_first": None, "vintage_last": None, "cohorts_total": None}
        m = self.runoff[event].months
        return {"vintage_first": month_text(int(m.min())), "vintage_last": month_text(int(m.max())),
                "cohorts_total": int(len(m))}

    def report_month(self, event: str) -> int | None:
        """The last month observed in the runoff data (yyyymm): every vintage is followed up to the
        report date, so this is the risk suite's LgdMaxDate. None without runoff data."""
        if event not in self.runoff:
            return None
        blk = self.runoff[event]
        m = blk.months
        idx = (m // 100) * 12 + m % 100 - 1 + np.where(blk.obs, np.arange(blk.obs.shape[1]), 0).max(axis=1)
        last = int(idx.max())
        return (last // 12) * 100 + last % 12 + 1

    def resolve_vintages(self, event: str, vintage_start: str | None, vintage_years: int | None,
                         report_date: str | None = None):
        """Turn the vintage parameters into one start month (yyyymm), or None for all vintages.

        'Last N years' is the N × 12 months that end with the report month: ``report_date`` when
        given, else the last month observed in the runoff data. A start at or before the first
        vintage keeps every vintage and is treated as no filter.
        Returns (start or None, info dict with the window, the report month and the exposure share)."""
        report = month_int(report_date) if report_date else self.report_month(event)
        info = {**self.vintage_summary(event), "vintage_filter": False, "vintage_start_effective": None,
                "cohorts_included": None, "exposure_share": None,
                "report_month": month_text(report) if report else None}
        if info["cohorts_total"] is not None:
            info["cohorts_included"] = info["cohorts_total"]
            info["exposure_share"] = 1.0
        if vintage_start is None and vintage_years is None:
            return None, info
        block = self.runoff_block(event)
        months = block.months
        last = int(months.max())
        if vintage_years is not None:
            y, m = report // 100 - int(vintage_years), report % 100 + 1
            start = (y + 1) * 100 + 1 if m == 13 else y * 100 + m
        else:
            start = month_int(vintage_start)
        if start > last:
            raise VintageError(f"Vintage start {month_text(start)} is after the last vintage {month_text(last)}; "
                               f"no cohorts remain")
        keep = months >= start
        if keep.all():
            return None, info
        at_default = block.X[:, 0]
        total = float(at_default.sum())
        info.update({"vintage_filter": True, "vintage_start_effective": month_text(start),
                     "cohorts_included": int(keep.sum()),
                     "exposure_share": float(at_default[keep].sum() / total) if total > 0 else 0.0})
        return start, info

    def triangles(self, event: str, vintage_start: int | None = None) -> Triangles:
        """Observed triangles for an EventType: read from lgd_recovery, or built from the runoff data
        for the vintages from ``vintage_start`` (yyyymm)."""
        key = (event, vintage_start)
        if key not in self._tri_cache:
            rate = self.implied_rate(event)
            if vintage_start is None:
                self._tri_cache[key] = _triangles_from_rows(self.block(event), rate)
            else:
                block = self.runoff_block(event)
                self._tri_cache[key] = vintage_triangles(block, block.months >= vintage_start, rate)
        return self._tri_cache[key]

    def rebuilt_block(self, event: str, vintage_start: int) -> np.ndarray:
        """The lgd_recovery rows for the vintages from ``vintage_start`` (yyyymm)."""
        block = self.runoff_block(event)
        return vintage_rows(block, block.months >= vintage_start, self.implied_rate(event))

    def implied_rate(self, event: str) -> float:
        a = self.block(event)
        hit = np.nonzero(a[:, _IX["DiscountIndex"]] == 1)[0]
        if hit.size == 0:
            raise ParseError("No row with DiscountIndex = 1; cannot derive the discount rate")
        df = a[hit[0], _IX["DiscountFactor"]]
        return float((1.0 / df) ** 12 - 1.0)

    def is_contiguous(self, event: str) -> bool:
        """True when every TermStep's rows are buckets ts, ts+1, ... with no gaps or repeats."""
        a = self.block(event)
        ts = a[:, _IX["TermStep"]].astype(int)
        b = a[:, _IX["BucketIndex"]].astype(int)
        order = np.lexsort((b, ts))
        ts, b = ts[order], b[order]
        first = np.r_[True, ts[1:] != ts[:-1]]
        ok_start = np.all(b[first] == ts[first])
        step = np.diff(b)
        ok_step = np.all(step[~first[1:]] == 1)
        return bool(ok_start and ok_step)

    def self_check(self) -> dict:
        """Rebuild the unfiltered triangles from the runoff data and compare them with the file.
        Sets and returns ``runoff_status``. A failing check keeps the data but disables the filter."""
        if not self.has_runoff:
            self.runoff_status = {"ok": False, "reason": "no runoff_triangle.csv in the zip"}
            return self.runoff_status
        events = [self.event_types[0]] if (self.identical_events and self.identical_runoff) else self.event_types
        for ev in events:
            if ev not in self.runoff:
                self.runoff_status = {"ok": False, "reason": f"runoff_triangle has no block for EventType '{ev}'"}
                return self.runoff_status
            file_tri = _triangles_from_rows(self.block(ev), self.implied_rate(ev))
            tri = self._all_vintages(ev, file_tri.implied_rate)
            problem = _compare_triangles(file_tri, tri)
            if problem:
                self.runoff_status = {"ok": False, "reason": f"EventType {ev}: {problem}"}
                return self.runoff_status
        self.runoff_status = {"ok": True, "reason": ""}
        return self.runoff_status

    def tie_out(self, event: str | None = None) -> dict:
        """Recompute the LGD from the observed buckets, before any extension, and measure how it
        ties to the risk suite's own figures. Reported at load so a reviewer can see, next to the
        rate in the file, that the data was read correctly.

        lgd_file_ts1 / lgd_replica_ts1: the TermStep 1 figures, which the risk suite reports at
        term 0 days, so they can be reconciled by eye. lgd_file / lgd_replica: exposure-weighted
        over the observed TermSteps, as the results page averages them. tie_max: largest |replica − (1 − CumulativeSumPV)| over the TermSteps.
        tie_max_vs_file: the same against the file's LGD column, which the risk suite floors at the
        previous TermStep's LGD (floor_count, floor_max). runoff_tie_max: the replica from the
        triangle rebuilt out of runoff_triangle.csv against the one from lgd_recovery.csv, or None
        when the zip has no usable run-off data."""
        ev = event or self.event_types[0]
        tri = self.triangles(ev)
        n = tri.n
        s = slice(1, n + 1)
        rep = replica_lgd(tri)
        w = np.diagonal(tri.E)[s]
        floor_gap = tri.lgd_file[s] - tri.lgd_unfloored[s]
        runoff_tie = None
        if self.vintage_filter_available:
            rebuilt = self._all_vintages(ev, tri.implied_rate)
            runoff_tie = max_abs(replica_lgd(rebuilt)[s] - rep[s])

        def first(col):
            return float(col[1]) if n >= 1 and np.isfinite(col[1]) else None

        return {
            "term_steps": n,
            "lgd_file_ts1": first(tri.lgd_file),      # the risk suite's LGD at term 0 days
            "lgd_replica_ts1": first(rep),
            "lgd_file": weighted_mean(w, tri.lgd_file[s]),
            "lgd_replica": weighted_mean(w, rep[s]),
            "tie_max": max_abs(rep[s] - tri.lgd_unfloored[s]),
            "tie_max_vs_file": max_abs(rep[s] - tri.lgd_file[s]),
            "floor_count": int((floor_gap[np.isfinite(floor_gap)] > 1e-12).sum()),
            "floor_max": max_abs(floor_gap),
            "runoff_tie_max": runoff_tie,
        }

    def _all_vintages(self, event: str, rate: float) -> Triangles:
        """The triangles built from the runoff data with every vintage kept."""
        block = self.runoff[event]
        return vintage_triangles(block, np.ones(len(block.cohort), dtype=bool), rate)

    def profile(self) -> dict:
        ev = self.event_types[0]
        tri = self.triangles(ev)
        a = self.block(ev)
        ts = a[:, _IX["TermStep"]].astype(int)
        opening = float(tri.E[1, 1]) if tri.n >= 1 else 0.0
        vint = self.vintage_summary(ev)
        report = self.report_month(ev)
        return {
            "tie_out": self.tie_out(ev),
            "category": self.category,
            "event_types": self.event_types,
            "identical_events": self.identical_events,
            "rows": int(sum(len(v) for v in self.raw.values())) if not self.identical_events
                    else int(len(a) * len(self.event_types)),
            "min_ts": int(ts.min()),
            "max_ts": int(ts.max()),
            "n": tri.n,
            "last_obs_bucket": tri.last_obs_file,
            "implied_rate": tri.implied_rate,
            "opening_exposure": opening,
            "contiguous": self.is_contiguous(ev),
            "has_runoff": self.has_runoff,
            "vintage_filter": self.vintage_filter_available,
            "vintage_reason": self.runoff_status.get("reason", "") if not self.vintage_filter_available else "",
            "cohort_first": vint["vintage_first"],
            "cohort_last": vint["vintage_last"],
            "cohort_count": vint["cohorts_total"],
            "report_month": month_text(report) if report else None,
        }

    # ----------------------------------------------------------- serialisation
    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        arrays = {}
        if self.identical_events:
            arrays["block_0"] = self.raw[self.event_types[0]]
        else:
            for i, ev in enumerate(self.event_types):
                arrays[f"block_{i}"] = self.raw[ev]
        runoff_events = [ev for ev in self.event_types if ev in self.runoff]
        if self.runoff:
            stored = runoff_events[:1] if self.identical_runoff else runoff_events
            for i, ev in enumerate(stored):
                blk = self.runoff[ev]
                arrays[f"runoff_cohort_{i}"] = blk.cohort.astype(np.int32)
                arrays[f"runoff_x_{i}"] = blk.X
                arrays[f"runoff_obs_{i}"] = blk.obs
        header = json.dumps({
            "category": self.category, "meta": self.meta,
            "event_types": self.event_types, "identical_events": self.identical_events,
            "runoff_events": runoff_events, "identical_runoff": self.identical_runoff,
            "runoff_status": self.runoff_status,
        })
        np.savez_compressed(buf, header=np.array(header), **arrays)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "RecoveryData":
        z = np.load(io.BytesIO(data), allow_pickle=False)
        h = json.loads(str(z["header"]))
        if h["identical_events"]:
            block = z["block_0"]
            raw = {ev: block for ev in h["event_types"]}
        else:
            raw = {ev: z[f"block_{i}"] for i, ev in enumerate(h["event_types"])}
        runoff: dict[str, RunoffBlock] = {}
        events = h.get("runoff_events") or []
        identical_runoff = bool(h.get("identical_runoff", True))
        if events:
            if identical_runoff:
                blk = RunoffBlock(cohort=z["runoff_cohort_0"], X=z["runoff_x_0"], obs=z["runoff_obs_0"].astype(bool))
                runoff = {ev: blk for ev in events}
            else:
                runoff = {ev: RunoffBlock(cohort=z[f"runoff_cohort_{i}"], X=z[f"runoff_x_{i}"],
                                          obs=z[f"runoff_obs_{i}"].astype(bool)) for i, ev in enumerate(events)}
        return cls(category=h["category"], meta=h["meta"], event_types=h["event_types"],
                   raw=raw, identical_events=h["identical_events"], runoff=runoff,
                   identical_runoff=identical_runoff, runoff_status=h.get("runoff_status") or {})


def _compare_triangles(file_tri: Triangles, tri: Triangles) -> str:
    """'' when the rebuilt triangles reproduce the file, else what differs."""
    if tri.n != file_tri.n:
        return f"the rebuilt triangle has {tri.n} TermSteps, the file {file_tri.n}"
    dR = float(np.max(np.abs(tri.R - file_tri.R)))
    if dR > SELF_CHECK_ATOL:
        return f"RecoveryPct differs by up to {dR:.2e}"
    scale = np.maximum(np.abs(file_tri.E), 1.0)
    dE = float(np.max(np.abs(tri.E - file_tri.E) / scale))
    if dE > SELF_CHECK_RTOL:
        return f"ExposureBucket differs by up to {dE:.2e} (relative)"
    for name, a, b in (("LGD", tri.lgd_file, file_tri.lgd_file),
                       ("1 − CumulativeSumPV", tri.lgd_unfloored, file_tri.lgd_unfloored)):
        if not np.array_equal(np.isnan(a), np.isnan(b)):
            return f"{name} is present for different TermSteps"
        ok = ~np.isnan(a)
        d = float(np.max(np.abs(a[ok] - b[ok]))) if ok.any() else 0.0
        if d > SELF_CHECK_ATOL:
            return f"{name} differs by up to {d:.2e}"
    if tri.last_obs_file != file_tri.last_obs_file:
        return f"the last observed bucket is {tri.last_obs_file} against {file_tri.last_obs_file} in the file"
    return ""


def from_raw_frame(df: pd.DataFrame, category: str = "", meta: dict | None = None,
                   runoff: pd.DataFrame | None = None) -> RecoveryData:
    """Build RecoveryData from a table with the 13 debug columns (and optionally the runoff table)."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ParseError("lgd_recovery is missing column(s): " + ", ".join(missing))
    if df.empty:
        raise ParseError("lgd_recovery has no rows")
    try:
        num = df[NUM_COLUMNS].astype(float)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"lgd_recovery has non-numeric values: {exc}") from None

    values = num.to_numpy()
    if not np.isfinite(values).all():
        raise ParseError("lgd_recovery has blank or non-numeric cells")
    if (values[:, _IX["DiscountFactor"]] <= 0).any():
        raise ParseError("DiscountFactor must be greater than zero")
    for col in ("TermStep", "BucketIndex"):
        v = values[:, _IX[col]]
        if (v != np.floor(v)).any() or v.min() < 1 or v.max() > MAX_TERMSTEP:
            raise ParseError(f"{col} must be whole numbers between 1 and {MAX_TERMSTEP}")

    event_col = df["EventType"].astype(str).to_numpy()
    event_types = list(dict.fromkeys(event_col))            # in order of appearance
    if len(event_types) > 20:
        raise ParseError("lgd_recovery has more than 20 EventTypes")
    raw = {ev: np.ascontiguousarray(values[event_col == ev]) for ev in event_types}

    first = raw[event_types[0]]
    identical = all(
        raw[ev].shape == first.shape and np.array_equal(raw[ev], first, equal_nan=True)
        for ev in event_types[1:]
    )
    if identical:
        raw = {ev: first for ev in event_types}
    data = RecoveryData(category=str(category), meta=meta or {}, event_types=event_types,
                        raw=raw, identical_events=identical)
    if runoff is not None:
        data.runoff, data.identical_runoff = runoff_blocks(runoff)
        data.self_check()
    return data


def runoff_blocks(df: pd.DataFrame) -> tuple[dict[str, RunoffBlock], bool]:
    """Parse the runoff table into one block per EventType. Returns (blocks, identical)."""
    missing = [c for c in RUNOFF_COLUMNS if c not in df.columns]
    if missing:
        raise ParseError("runoff_triangle is missing column(s): " + ", ".join(missing))
    if df.empty:
        raise ParseError("runoff_triangle has no rows")
    dates = pd.to_datetime(df["CohortDate"], errors="coerce")
    if dates.isna().any():
        raise ParseError("runoff_triangle has a CohortDate that is not a date")
    cohort_all = (dates.dt.year * 10000 + dates.dt.month * 100 + dates.dt.day).to_numpy().astype(np.int64)
    try:
        bucket_all = df["Bucket"].astype(float).to_numpy()
        expo_all = df["ExposureAmount"].astype(float).to_numpy()
    except (TypeError, ValueError) as exc:
        raise ParseError(f"runoff_triangle has non-numeric values: {exc}") from None
    if not np.isfinite(expo_all).all():
        raise ParseError("runoff_triangle has blank or non-numeric ExposureAmount values")
    if (not np.isfinite(bucket_all).all() or (bucket_all != np.floor(bucket_all)).any()
            or bucket_all.min() < 0 or bucket_all.max() > MAX_TERMSTEP):
        raise ParseError(f"runoff_triangle Bucket must be whole numbers between 0 and {MAX_TERMSTEP}")
    bucket_all = bucket_all.astype(int)
    event_col = df["EventType"].astype(str).to_numpy()
    events = list(dict.fromkeys(event_col))
    if len(events) > 20:
        raise ParseError("runoff_triangle has more than 20 EventTypes")
    blocks: dict[str, RunoffBlock] = {}
    for ev in events:
        sel = event_col == ev
        cohorts, inv = np.unique(cohort_all[sel], return_inverse=True)
        if len(cohorts) > MAX_COHORTS:
            raise ParseError(f"runoff_triangle has more than {MAX_COHORTS} vintages")
        K = int(bucket_all[sel].max()) + 1
        X = np.zeros((len(cohorts), K))
        obs = np.zeros((len(cohorts), K), dtype=bool)
        np.add.at(X, (inv, bucket_all[sel]), expo_all[sel])
        obs[inv, bucket_all[sel]] = True
        blocks[ev] = RunoffBlock(cohort=cohorts.astype(np.int32), X=X, obs=obs)
    first = blocks[events[0]]
    identical = all(
        blocks[ev].X.shape == first.X.shape and np.array_equal(blocks[ev].cohort, first.cohort)
        and np.array_equal(blocks[ev].X, first.X) and np.array_equal(blocks[ev].obs, first.obs)
        for ev in events[1:])
    if identical:
        blocks = {ev: first for ev in events}
    return blocks, identical


def _find_member(zf: zipfile.ZipFile, name: str) -> str | None:
    for info in zf.infolist():
        if info.filename.replace("\\", "/").split("/")[-1].lower() == name:
            return info.filename
    return None


def _no_constants(name: str):
    raise ValueError(f"{name} is not allowed in debug.json")


def parse_zip(source: str | BinaryIO) -> RecoveryData:
    """Parse a debug zip (path or binary file object)."""
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        raise ParseError("Not a valid zip file") from None
    with zf:
        csv_name = _find_member(zf, "lgd_recovery.csv")
        if csv_name is None:
            raise ParseError("The zip does not contain lgd_recovery.csv")
        if zf.getinfo(csv_name).file_size > MAX_CSV_BYTES:
            raise ParseError("lgd_recovery.csv is larger than 600 MB uncompressed")
        meta: dict = {}
        json_name = _find_member(zf, "debug.json")
        if json_name is not None and zf.getinfo(json_name).file_size > MAX_JSON_BYTES:
            raise ParseError("debug.json is larger than 5 MB")
        if json_name is not None:
            try:
                meta = json.loads(zf.read(json_name).decode("utf-8-sig"), parse_constant=_no_constants)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise ParseError("debug.json is not valid JSON") from None
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, OSError):
                raise ParseError("debug.json could not be read from the zip (encrypted or damaged)") from None
            if not isinstance(meta, dict):
                raise ParseError("debug.json must hold a JSON object")
        try:
            with zf.open(csv_name) as fh:
                df = pd.read_csv(fh, float_precision="round_trip")
        except Exception as exc:  # pandas and zipfile raise several error types
            raise ParseError(f"lgd_recovery.csv could not be read: {exc}") from None
        runoff = None
        runoff_name = _find_member(zf, "runoff_triangle.csv")
        if runoff_name is not None:
            if zf.getinfo(runoff_name).file_size > MAX_RUNOFF_BYTES:
                raise ParseError("runoff_triangle.csv is larger than 200 MB uncompressed")
            try:
                with zf.open(runoff_name) as fh:
                    runoff = pd.read_csv(fh, float_precision="round_trip")
            except Exception as exc:
                raise ParseError(f"runoff_triangle.csv could not be read: {exc}") from None
    category = str(meta.get("Category1", "")).strip()[:100]
    return from_raw_frame(df, category=category, meta=meta, runoff=runoff)
