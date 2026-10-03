"""Read a risk-suite debug zip into a compact, serialisable RecoveryData object.

Only two members of the zip are used: ``lgd_recovery.csv`` and ``debug.json``.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np
import pandas as pd

COLUMNS = [
    "EventType", "TermStep", "TermDays", "ExposureBucket", "BucketIndex",
    "PrevColSum", "ThisColSum", "RecoveryPct", "DiscountIndex",
    "DiscountFactor", "Contribution", "CumulativeSumPV", "LGD",
]
NUM_COLUMNS = COLUMNS[1:]
_IX = {name: i for i, name in enumerate(NUM_COLUMNS)}

MAX_TERMSTEP = 2000                 # triangles are (n+1) x (n+1); this caps them at 32 MB each
MAX_CSV_BYTES = 600 * 1024 * 1024   # uncompressed lgd_recovery.csv (the largest seen is 31 MB)
MAX_JSON_BYTES = 5 * 1024 * 1024


class ParseError(ValueError):
    """The uploaded file is not a usable debug zip."""


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
class RecoveryData:
    category: str
    meta: dict
    event_types: list[str]
    raw: dict[str, np.ndarray]            # event type -> (rows, 12) numeric columns in file order
    identical_events: bool = True
    _tri_cache: dict = field(default_factory=dict, repr=False, compare=False)

    # ------------------------------------------------------------------ access
    def block(self, event: str) -> np.ndarray:
        if event not in self.raw:
            raise ParseError(
                f"EventType '{event}' is not in this file (available: {', '.join(self.event_types)})")
        return self.raw[event]

    def triangles(self, event: str) -> Triangles:
        if event in self._tri_cache:
            return self._tri_cache[event]
        a = self.block(event)
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

        tri = Triangles(n=n, R=R, E=E, lgd_file=lgd_file, lgd_unfloored=lgd_unfloored,
                        last_obs_file=last_obs_file,
                        implied_rate=self.implied_rate(event))
        self._tri_cache[event] = tri
        return tri

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

    def profile(self) -> dict:
        ev = self.event_types[0]
        tri = self.triangles(ev)
        a = self.block(ev)
        ts = a[:, _IX["TermStep"]].astype(int)
        opening = float(tri.E[1, 1]) if tri.n >= 1 else 0.0
        return {
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
        header = json.dumps({
            "category": self.category, "meta": self.meta,
            "event_types": self.event_types, "identical_events": self.identical_events,
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
        return cls(category=h["category"], meta=h["meta"], event_types=h["event_types"],
                   raw=raw, identical_events=h["identical_events"])


def from_raw_frame(df: pd.DataFrame, category: str = "", meta: dict | None = None) -> RecoveryData:
    """Build RecoveryData from a table with the 13 debug columns."""
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
    return RecoveryData(category=str(category), meta=meta or {}, event_types=event_types,
                        raw=raw, identical_events=identical)


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
    category = str(meta.get("Category1", "")).strip()[:100]
    return from_raw_frame(df, category=category, meta=meta)
