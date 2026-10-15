"""Monitoring policies: two NEWS2 baselines and the VeriLoop service loop.

VeriLoop runs one loop iteration per sample:

    sense -> interpret (signal-quality / fault diagnosis, personalised
    features, risk model) -> decide (tiered service contract with NEWS2
    guardrails) -> execute (patient, device or clinician request) ->
    validate (confirm the data, verify the outcome of every clinical
    action, feed outcomes back into per-patient operating rules)

and every iteration is appended to a hash-chained provenance ledger.
Ablation switches turn individual loop stages off.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass

import numpy as np

from .physio import N_CH, PLAUSIBLE_HI, PLAUSIBLE_LO, STEPS_PER_DAY, news2
from .sim import DEVICE_CHECK, ESCALATE, REMEASURE, TELECONSULT

SPIKE_THR = np.array([25, 8, 6, 30, 1.0])
STUCK_WIN = np.array([6, 8, 12, 6, 48])
SD_FLOOR = np.array([3.0, 1.5, 1.0, 5.0, 0.2])
# Direction in which each channel moves when a patient deteriorates.
DIRECTION = np.array([+1, +1, -1, -1, +1])
MSG_RAW, MSG_SUMMARY, MSG_EVENT, MSG_ANCHOR = 100, 120, 200, 64


def range_check(x):
    x = x.copy()
    bad = (x < PLAUSIBLE_LO) | (x > PLAUSIBLE_HI)
    x[bad] = np.nan
    return x, bad


# --------------------------------------------------------------- features
class FeatureTracker:
    """Personal baseline (24 h calibration) and windowed z-score features."""

    N_FEAT = 3 * N_CH + 1

    def __init__(self, scale2: bool):
        self.scale2 = scale2
        self.calib = []
        self.mu = None
        self.sd = None
        self.z = deque(maxlen=12)
        self.last_val = np.full(N_CH, np.nan)
        self.last_age = np.full(N_CH, 999)
        self.recent = deque(maxlen=24)

    @property
    def calibrated(self):
        return self.mu is not None

    def update(self, t, clean):
        """clean: cleaned observation (NaN = unusable) or None (not sampled)."""
        if clean is not None:
            ok = ~np.isnan(clean)
            self.last_val[ok] = clean[ok]
            self.last_age[ok] = 0
            self.last_age[~ok] += 1
            if not self.calibrated:
                self.calib.append(clean)
            self.recent.append(clean)
        else:
            self.last_age += 1
        if not self.calibrated and t >= STEPS_PER_DAY - 1 and len(self.calib) > 20:
            arr = np.array(self.calib)
            self.mu = np.nanmedian(arr, axis=0)
            mad = np.nanmedian(np.abs(arr - self.mu), axis=0) * 1.4826
            self.sd = np.maximum(np.nan_to_num(mad, nan=1.0), SD_FLOOR)
        cur = self.current()
        if self.calibrated:
            zz = (cur - self.mu) / self.sd
            self.z.append(np.nan_to_num(zz, nan=0.0))

    def current(self):
        v = self.last_val.copy()
        v[self.last_age > 6] = np.nan          # do not carry values > 30 min
        return v

    def features(self, override=None):
        if not self.calibrated or len(self.z) == 0:
            return None
        Z = np.array(self.z)
        znow = Z[-1] if override is None else np.nan_to_num((override - self.mu) / self.sd)
        mean1h = Z.mean(axis=0)
        slope = Z[-3:].mean(axis=0) - Z[:3].mean(axis=0)
        cur = self.current() if override is None else override
        agg, _ = news2(cur, self.scale2)
        return np.concatenate([znow * DIRECTION, mean1h * DIRECTION, slope * DIRECTION, [agg]])

    def refresh_baseline(self, w=0.2):
        if not self.calibrated or len(self.recent) < 6:
            return
        arr = np.array(self.recent)
        if np.isnan(arr).all(axis=0).any():
            return
        rec = np.nanmedian(arr, axis=0)
        ok = ~np.isnan(rec)
        self.mu[ok] = (1 - w) * self.mu[ok] + w * rec[ok]


@dataclass
class RiskModel:
    coef: np.ndarray
    intercept: float
    mean: np.ndarray
    scale: np.ndarray

    def __call__(self, f):
        if f is None:
            return 0.0
        s = float(((f - self.mean) / self.scale) @ self.coef + self.intercept)
        return 1.0 / (1.0 + np.exp(-s))


# --------------------------------------------------------------- baselines
class SpotNEWS2:
    """Periodic telemonitoring: one NEWS2 spot check every 4 h, scored in the cloud."""
    edge = False
    name = "Spot-check NEWS2"

    def __init__(self, scale2, period=48, offset=0):
        self.scale2, self.period, self.offset = scale2, period, offset
        self.uplink_bytes = self.uplink_msgs = 0

    def wants_sample(self, t):
        return (t - self.offset) % self.period == 0

    def feedback(self, t, fb):
        return []

    def observe(self, t, t_obs, obs):
        if obs is None:
            return []
        self.uplink_bytes += MSG_RAW
        self.uplink_msgs += 1
        x, _ = range_check(obs)
        agg, mx = news2(x, self.scale2)
        if agg >= 7:
            return [dict(type=ESCALATE, src="news")]
        if agg >= 5 or mx == 3:
            return [dict(type=TELECONSULT, src="news")]
        return []


class ContNEWS2:
    """Continuous streaming NEWS2 in the cloud with a 10-min persistence
    filter and a 4-h alert cooldown (common alarm-fatigue mitigations)."""
    edge = False
    name = "Continuous NEWS2"

    def __init__(self, scale2, cooldown=48):
        self.scale2, self.cooldown = scale2, cooldown
        self.amber = self.red = 0
        self.cool_until = {2: -1, 3: -1}
        self.uplink_bytes = self.uplink_msgs = 0

    def wants_sample(self, t):
        return True

    def feedback(self, t, fb):
        return []

    def observe(self, t, t_obs, obs):
        if obs is None:
            return []
        self.uplink_bytes += MSG_RAW
        self.uplink_msgs += 1
        x, _ = range_check(obs)
        if np.isnan(x).all():
            return []
        agg, mx = news2(x, self.scale2)
        self.red = self.red + 1 if agg >= 7 else 0
        self.amber = self.amber + 1 if (agg >= 5 or mx == 3) else 0
        if self.red >= 2 and t >= self.cool_until[3]:
            self.cool_until[3] = self.cool_until[2] = t + self.cooldown
            return [dict(type=ESCALATE, src="news")]
        if self.amber >= 2 and t >= self.cool_until[2]:
            self.cool_until[2] = t + self.cooldown
            return [dict(type=TELECONSULT, src="news")]
        return []


# ----------------------------------------------------------------- VeriLoop
class Ledger:
    """Append-only SHA-256 hash chain with hourly anchors sent to the cloud."""

    def __init__(self, keep=False):
        self.prev = "0" * 64
        self.n = 0
        self.bytes = 0
        self.keep = keep
        self.records = []
        self.anchors = []

    def append(self, rec: dict):
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256((self.prev + body).encode()).hexdigest()
        if self.keep:
            self.records.append((body, h))
        self.prev = h
        self.n += 1
        self.bytes += len(body) + 64

    def anchor(self, t):
        if self.n:
            self.anchors.append((self.n - 1, self.prev))


def verify_chain(records, anchors_by_index=None):
    prev = "0" * 64
    for body, h in records:
        if hashlib.sha256((prev + body).encode()).hexdigest() != h:
            return False
        prev = h
    if anchors_by_index:
        for idx, h in anchors_by_index:
            if idx >= len(records) or records[idx][1] != h:
                return False
    return True


class VeriLoop:
    def __init__(self, scale2, model: RiskModel, fault_dx=True, validation=True, adapt=True,
                 edge=True, adaptive_sampling=True, keep_ledger=False, name="VeriLoop"):
        self.scale2, self.model = scale2, model
        self.fault_dx, self.validation, self.adapt = fault_dx, validation, adapt
        self.edge, self.adaptive_sampling, self.name = edge, adaptive_sampling, name
        self.ft = FeatureTracker(scale2)
        self.hist = deque(maxlen=int(STUCK_WIN.max()))
        self.hold = np.full(N_CH, np.nan)           # spike candidates held for one sample
        self.theta = 0.5
        self.amber_thr = 5
        self.persist = {2: 0, 3: 0}
        self.cool_until = {2: -1, 3: -1}
        self.cool_ref_r = 1.0
        self.awaiting_clin = {}
        self.awaiting_rm = None
        self.verify = []
        self.missing_run = np.zeros(N_CH, int)
        self.suspect = np.zeros(N_CH, bool)
        self.last_device_check = -999
        self.high_rate_until = -1
        self.r = 0.0
        self.r_hist = deque(maxlen=6)
        self.news_hist = deque(maxlen=6)
        self.current_flags = np.zeros(N_CH, bool)
        self.ledger = Ledger(keep=keep_ledger)
        self.uplink_bytes = self.uplink_msgs = 0
        self.next_id = 0

    # ------------------------------------------------------------ sensing
    def wants_sample(self, t):
        if not self.adaptive_sampling or t < STEPS_PER_DAY or t <= self.high_rate_until:
            return True
        return t % 3 == 0

    def _uplink(self, nbytes):
        self.uplink_bytes += nbytes
        self.uplink_msgs += 1

    # ------------------------------------------------------- interpretation
    def _clean(self, obs):
        x, bad = range_check(obs)
        flags = bad & ~np.isnan(obs)
        if not self.fault_dx:
            return x, flags
        hist = np.array(self.hist) if self.hist else None
        for c in range(N_CH):
            if np.isnan(x[c]):
                continue
            if hist is not None and len(hist) >= 3:
                col = hist[:, c]
                col = col[~np.isnan(col)][-4:]
                if len(col) >= 3:
                    med = np.median(col)
                    if abs(x[c] - med) > SPIKE_THR[c]:
                        # Accept a jump only if the next sample confirms it.
                        if not np.isnan(self.hold[c]) and abs(x[c] - self.hold[c]) <= SPIKE_THR[c] / 2:
                            self.hold[c] = np.nan
                        else:
                            self.hold[c] = x[c]
                            flags[c] = True
                            x[c] = np.nan
                            continue
                    else:
                        self.hold[c] = np.nan
            w = STUCK_WIN[c]
            if hist is not None and len(hist) >= w and not (c == 2 and x[c] >= 99):
                tail = hist[-(w - 1):, c]
                if np.all(tail == x[c]):
                    flags[c] = True
                    x[c] = np.nan
        return x, flags

    def _abnormal_channels(self, cur):
        if not self.ft.calibrated:
            return np.zeros(N_CH, bool)
        z = np.nan_to_num((cur - self.ft.mu) / self.ft.sd) * DIRECTION
        return z > 2.5

    # -------------------------------------------------------------- loop
    def observe(self, t, t_obs, obs):
        actions = []
        if obs is not None:
            raw = obs
            clean, flags = self._clean(obs)
            self.hist.append(raw)
            missing = np.isnan(raw) | (flags & (raw < PLAUSIBLE_LO))
            self.missing_run = np.where(missing, self.missing_run + 1, 0)
        else:
            clean, flags = None, np.zeros(N_CH, bool)
        self.ft.update(t, clean)
        cur = self.ft.current()
        f = self.ft.features()
        if not np.isnan(cur).all():             # no usable data: hold the last risk estimate
            self.r = self.model(f) if f is not None else 0.0
        agg, mx = news2(cur, self.scale2)
        self.r_hist.append(self.r)
        self.news_hist.append(agg)
        abn = self._abnormal_channels(cur)

        # Uncorroborated single-channel deviations are treated as suspect data.
        if self.fault_dx and self.ft.calibrated and obs is not None:
            single = abn.sum() == 1 and abn[[2, 3]].any()
            self.suspect = abn & single
            flags = flags | self.suspect
        self.current_flags = flags if obs is not None else self.current_flags

        calibrated = self.ft.calibrated
        red = agg >= 7 or (calibrated and self.r >= 0.9)
        amber = agg >= self.amber_thr or mx == 3 or (calibrated and self.r >= self.theta)
        if obs is not None:
            self.persist[3] = self.persist[3] + 1 if red else 0
            self.persist[2] = self.persist[2] + 1 if amber else 0
        if amber or red or self.r > 0.2:
            self.high_rate_until = t + 12      # elevated state: full-rate sensing and raw uplink

        # --- execute: device maintenance (closing the sensing loop)
        if self.fault_dx and (self.missing_run >= 6).any() and t - self.last_device_check > 24:
            self.last_device_check = t
            actions.append(self._act(DEVICE_CHECK, t, "missing"))
        if self.fault_dx and self.suspect.any() and self.awaiting_rm is None and t - self.last_device_check > 24 \
                and not (amber or red):
            self.last_device_check = t
            actions.append(self._act(DEVICE_CHECK, t, "suspect"))

        # --- validate: outcome verification of clinical actions
        if self.validation:
            for v in list(self.verify):
                if t >= v["due"]:
                    self.verify.remove(v)
                    r_now = float(np.mean(list(self.r_hist)[-3:]))
                    n_now = float(np.mean(list(self.news_hist)[-3:]))
                    ok = (r_now <= v["r_ref"] - 0.15) or (n_now <= v["n_ref"] - 2) or (r_now < 0.3 and n_now < 5)
                    self._log(t, "verify", dict(ok=bool(ok), r=round(r_now, 3)))
                    if not ok:
                        actions.append(self._act(ESCALATE, t, "verify"))
                        self.awaiting_clin["verify"] = t
                        self.persist = {2: 0, 3: 0}
                    else:
                        self.cool_until[2] = t + 36
                        self.cool_ref_r = v["r_ref"]

        # --- decide
        tier = 0
        if self.persist[3] >= 2:
            tier = 3
        elif self.persist[2] >= 2:
            tier = 2
        if tier == 2 and t < self.cool_until[2] and not (self.validation and self.r >= self.cool_ref_r + 0.15):
            tier = 0
        if tier == 3 and t < self.cool_until[3]:
            tier = 0
        if self.awaiting_clin and tier == 2:
            tier = 0
        if tier and calibrated and t >= STEPS_PER_DAY:
            corroborated = abn.sum() >= 2
            if self.fault_dx and (tier == 2 or not corroborated):
                if self.awaiting_rm is None:
                    self.awaiting_rm = dict(t=t, tier=tier)
                    actions.append(self._act(REMEASURE, t, "confirm"))
            else:
                actions.append(self._issue(t, tier))
        elif tier and not calibrated:
            actions.append(self._issue(t, tier))

        # --- uplink and provenance
        if self.edge:
            if t <= self.high_rate_until and obs is not None:
                self._uplink(MSG_RAW)
            elif t % 6 == 0:
                self._uplink(MSG_SUMMARY)
            if t % 12 == 0:
                self.ledger.anchor(t)
                self._uplink(MSG_ANCHOR)
        elif obs is not None:
            self._uplink(MSG_RAW)
        self._log(t, "iter", dict(obs=None if obs is None else hashlib.sha1(obs.tobytes()).hexdigest()[:16],
                                  flags=int(np.packbits(flags.astype(np.uint8))[0]), r=round(self.r, 3),
                                  news=agg, tier=tier, theta=round(self.theta, 2)))
        for a in actions:
            if self.edge:
                self._uplink(MSG_EVENT)
        return actions

    def _issue(self, t, tier):
        kind = TELECONSULT if tier == 2 else ESCALATE
        self.awaiting_clin[kind] = t
        self.persist = {2: 0, 3: 0}
        if not self.validation:
            self.cool_until[2] = t + 48
            if tier == 3:
                self.cool_until[3] = t + 48
        else:
            self.cool_until[2] = t + 6         # suppress until the clinician answers
            if tier == 3:
                self.cool_until[3] = t + 6
        a = self._act(kind, t, "loop")
        a["r_ref"], a["n_ref"] = self.r, float(np.mean(self.news_hist))
        self._pending_ref = (a["id"], self.r, float(np.mean(self.news_hist)))
        return a

    def _act(self, kind, t, src):
        self.next_id += 1
        self._log(t, "action", dict(kind=kind, src=src, id=self.next_id))
        return dict(type=kind, src=src, id=self.next_id)

    def _log(self, t, kind, payload):
        self.ledger.append(dict(t=t, k=kind, **payload))

    # ----------------------------------------------------------- feedback
    def feedback(self, t, fb):
        actions = []
        kind = fb["type"]
        self._log(t, "feedback", dict(kind=kind, finding=fb.get("finding", "")))
        if kind == "clinician":
            self.awaiting_clin = {}
            if fb["finding"] == "treated":
                if self.validation:
                    self.verify.append(dict(due=t + 18, r_ref=max(self.r, 0.5), n_ref=float(np.mean(self.news_hist))))
                    self.cool_until[2] = max(self.cool_until[2], t + 18)
                    self.cool_until[3] = max(self.cool_until[3], t + 18)
            else:
                if self.validation:
                    self.cool_until[2] = t + 24
                    self.cool_ref_r = self.r
                if self.adapt and fb["finding"] == "false_alarm":
                    if self.r >= self.theta - 0.05:
                        self.theta = min(0.85, self.theta + 0.05)
                    agg = self.news_hist[-1] if self.news_hist else 0
                    if agg >= self.amber_thr:
                        self.amber_thr = min(6, self.amber_thr + 1)
                    self.ft.refresh_baseline()
        elif kind in ("remeasure", "remeasure_none") and self.awaiting_rm is not None:
            pend = self.awaiting_rm
            self.awaiting_rm = None
            if kind == "remeasure_none":
                actions.append(self._issue(t, pend["tier"]))
            else:
                v = fb["values"]
                agg, mx = news2(v, self.scale2)
                r_rm = self.model(self.ft.features(override=v))
                abn = self._abnormal_channels(v)
                if agg >= 7 or (r_rm >= 0.9 and abn.sum() >= 2):
                    actions.append(self._issue(t, 3))
                elif agg >= self.amber_thr or mx == 3 or r_rm >= self.theta:
                    actions.append(self._issue(t, 2))
                else:
                    # Not confirmed: wearable data disagreed with the spot check.
                    self.persist = {2: 0, 3: 0}
                    self.cool_until[2] = t + 12
                    self.cool_ref_r = self.r
                    if self.suspect.any() and t - self.last_device_check > 24:
                        self.last_device_check = t
                        actions.append(self._act(DEVICE_CHECK, t, "mismatch"))
        elif kind == "device_fixed":
            self.hold[:] = np.nan
            self.missing_run[:] = 0
        return actions
