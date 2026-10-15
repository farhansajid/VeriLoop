"""Synthetic physiology, sensor-fault and connectivity models for a
home remote-patient-monitoring (RPM) cohort.

Everything runs at a 5-minute resolution (288 steps per day).  Vital-sign
channels, in this order everywhere in the code:

    0 HR   heart rate            [beats/min]
    1 RR   respiratory rate      [breaths/min]
    2 SPO2 peripheral O2 sat.    [%]
    3 SBP  systolic BP           [mmHg]
    4 TEMP body temperature      [deg C]
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

STEP_MIN = 5
STEPS_PER_DAY = 24 * 60 // STEP_MIN
CH = ["HR", "RR", "SPO2", "SBP", "TEMP"]
N_CH = len(CH)

# Plausibility limits used by every pipeline's range check.
PLAUSIBLE_LO = np.array([25, 4, 50, 50, 32.0])
PLAUSIBLE_HI = np.array([230, 60, 100, 260, 43.0])
# Display quantisation of commercial devices.
QUANT = np.array([1, 1, 1, 1, 0.1])

# Physiological change at full deterioration (progress p = 1) for the two
# deterioration phenotypes.  Signs follow the clinical picture.
DELTA_SEPTIC = np.array([+42, +10, -6, -42, +1.7])
DELTA_RESP = np.array([+30, +14, -11, -18, +0.6])

# Patient sub-groups: name, share, baseline means, between-patient sd.
GROUPS = [
    ("general", 0.50, [74, 15, 97, 128, 36.7], [7, 1.5, 1.0, 10, 0.2]),
    ("copd", 0.20, [84, 19, 91, 132, 36.7], [7, 1.5, 1.2, 10, 0.2]),
    ("heart_failure", 0.15, [60, 16, 96, 106, 36.6], [4, 1.5, 1.0, 6, 0.2]),
    ("tachy_hypertensive", 0.15, [94, 17, 97, 158, 36.8], [5, 1.5, 1.0, 10, 0.2]),
]
NOISE_SD = np.array([3.0, 1.2, 0.8, 6.0, 0.08])
AR_COEF = np.array([0.85, 0.8, 0.8, 0.8, 0.95])
CIRCADIAN_AMP = np.array([6, 1.0, 0.3, 8, 0.25])


@dataclass
class Episode:
    onset: int          # step at which deterioration starts
    ramp: int           # steps from onset to critical if untreated
    delta: np.ndarray   # channel deltas at p = 1
    phenotype: str


@dataclass
class Fault:
    kind: str           # spike | dropout | stuck | drift | detached
    ch: int             # channel (-1 = whole device)
    start: int
    end: int            # exclusive; may be shortened by a repair
    mag: float = 0.0
    value: float = np.nan


@dataclass
class Patient:
    pid: int
    group: str
    copd_scale2: bool
    base_mean: np.ndarray
    base: np.ndarray            # [T, N_CH] physiology without deterioration
    episodes: list[Episode]
    faults: list[Fault]
    outage: np.ndarray          # [T] bool, home internet down
    activity: np.ndarray        # [T] bool, benign exertion transient
    meta: dict = field(default_factory=dict)


def _ar_noise(rng, T, sd, phi):
    e = rng.normal(0, 1, size=(T, len(sd))) * sd * np.sqrt(1 - phi ** 2)
    x = np.zeros_like(e)
    x[0] = rng.normal(0, sd)
    for t in range(1, T):
        x[t] = phi * x[t - 1] + e[t]
    return x


def make_patient(pid: int, rng: np.random.Generator, days: int = 7,
                 with_episodes: bool = True) -> Patient:
    T = days * STEPS_PER_DAY
    shares = np.array([g[1] for g in GROUPS])
    gi = rng.choice(len(GROUPS), p=shares / shares.sum())
    gname, _, gmean, gsd = GROUPS[gi]
    mean = np.array(gmean, float) + rng.normal(0, 1, N_CH) * np.array(gsd)
    mean[2] = min(mean[2], 99.0)

    tod = (np.arange(T) % STEPS_PER_DAY) / STEPS_PER_DAY
    # Nadir around 04:00, peak in the afternoon.
    circ = np.sin(2 * np.pi * (tod - 0.42))[:, None] * CIRCADIAN_AMP
    base = mean + circ + _ar_noise(rng, T, NOISE_SD * rng.uniform(0.8, 1.2), AR_COEF)

    # Benign exertion transients (walking, stairs, anxiety): daytime only.
    activity = np.zeros(T, bool)
    n_act = rng.poisson(1.5 * days)
    for _ in range(n_act):
        day = rng.integers(0, days)
        st = day * STEPS_PER_DAY + rng.integers(int(0.33 * STEPS_PER_DAY), int(0.88 * STEPS_PER_DAY))
        du = rng.integers(3, 9)
        sl = slice(st, min(T, st + du))
        k = rng.uniform(0.6, 1.0)
        base[sl, 0] += k * rng.uniform(25, 45)
        base[sl, 1] += k * rng.uniform(4, 9)
        base[sl, 3] += k * rng.uniform(10, 25)
        base[sl, 2] -= k * rng.uniform(0, 2)
        activity[sl] = True

    # Deterioration episodes start after the 24 h calibration day.
    episodes = []
    if with_episodes:
        n_ep = rng.poisson(0.55)
        t = STEPS_PER_DAY + rng.integers(0, STEPS_PER_DAY)
        for _ in range(n_ep):
            if t >= T - 12 * 12:
                break
            ramp = int(rng.uniform(3, 10) * 12)          # 3-10 h to critical
            ph = "septic" if rng.random() < 0.5 else "resp"
            d = (DELTA_SEPTIC if ph == "septic" else DELTA_RESP) * rng.uniform(0.7, 1.3, N_CH)
            episodes.append(Episode(int(t), ramp, d, ph))
            t += ramp + int(rng.uniform(1, 3) * STEPS_PER_DAY)

    faults = make_faults(rng, T, days)

    outage = np.zeros(T, bool)
    for _ in range(rng.poisson(0.5 * days)):
        st = rng.integers(0, T)
        du = max(2, int(rng.lognormal(np.log(9), 0.7)))   # median 45 min
        outage[st:st + du] = True

    return Patient(pid, gname, gname == "copd", mean, base, episodes, faults, outage, activity)


def make_faults(rng, T, days):
    f: list[Fault] = []
    # Motion artefacts: single-sample spikes, mostly on PPG channels.
    for ch, rate, mag in [(0, 2.0, 55), (2, 2.0, -16), (1, 1.0, 14), (3, 0.5, 45), (4, 0.3, -2.5)]:
        for _ in range(rng.poisson(rate * days)):
            st = int(rng.integers(0, T))
            sign = 1 if ch in (2, 4) else rng.choice([-1, 1])
            f.append(Fault("spike", ch, st, st + 1, mag=sign * mag * rng.uniform(0.7, 1.3)))
    # Packet loss / wearable off: whole-device dropout.
    for _ in range(rng.poisson(1.0 * days)):
        st = int(rng.integers(0, T))
        du = max(1, int(rng.lognormal(np.log(6), 0.8)))
        f.append(Fault("dropout", -1, st, st + du))
    # Frozen channel (firmware / buffer stall).
    for _ in range(rng.poisson(0.15 * days)):
        st = int(rng.integers(0, T))
        f.append(Fault("stuck", int(rng.integers(0, N_CH)), st, st + int(rng.integers(12, 72))))
    # Probe misplacement: slow SpO2 under-reading or BP cuff offset.
    for ch, rate, lo, hi in [(2, 0.2, -10, -5), (3, 0.1, -30, -15)]:
        for _ in range(rng.poisson(rate * days)):
            st = int(rng.integers(0, T))
            f.append(Fault("drift", ch, st, st + int(rng.integers(24, 96)), mag=rng.uniform(lo, hi)))
    # Detached probe: implausible constant value.
    for ch, val in [(2, 0.0), (4, 30.5)]:
        for _ in range(rng.poisson(0.1 * days)):
            st = int(rng.integers(0, T))
            f.append(Fault("detached", ch, st, st + int(rng.integers(36, 144)), value=val))
    return f


def news2(x: np.ndarray, scale2: bool = False) -> tuple[int, int]:
    """NEWS2 (RCP 2017) for the five measured parameters.

    Consciousness and supplemental oxygen are not captured by the wearable
    and are assumed normal (alert, room air).  Missing channels score 0.
    Returns (aggregate score, max single-parameter score)."""
    hr, rr, sp, sbp, tp = x
    s = []
    s.append(0 if np.isnan(rr) else 3 if rr <= 8 else 1 if rr <= 11 else 0 if rr <= 20 else 2 if rr <= 24 else 3)
    if np.isnan(sp):
        s.append(0)
    elif scale2:
        s.append(3 if sp <= 83 else 2 if sp <= 85 else 1 if sp <= 87 else 0)
    else:
        s.append(3 if sp <= 91 else 2 if sp <= 93 else 1 if sp <= 95 else 0)
    s.append(0 if np.isnan(sbp) else 3 if sbp <= 90 else 2 if sbp <= 100 else 1 if sbp <= 110 else 0 if sbp <= 219 else 3)
    s.append(0 if np.isnan(hr) else 3 if hr <= 40 else 1 if hr <= 50 else 0 if hr <= 90 else 1 if hr <= 110 else 2 if hr <= 130 else 3)
    s.append(0 if np.isnan(tp) else 3 if tp <= 35.0 else 1 if tp <= 36.0 else 0 if tp <= 38.0 else 1 if tp <= 39.0 else 2)
    return int(sum(s)), int(max(s))


def quantise(x):
    return np.round(x / QUANT) * QUANT
