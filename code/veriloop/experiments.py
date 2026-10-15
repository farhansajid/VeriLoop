"""Run every monitoring configuration on the same virtual cohort."""
from __future__ import annotations

import numpy as np

from .physio import STEPS_PER_DAY, make_patient
from .policies import ContNEWS2, SpotNEWS2, VeriLoop
from .sim import Env

CONFIGS = {
    "spot_news2": dict(label="Spot-check NEWS2 (4 h)", group="baseline"),
    "cont_news2": dict(label="Continuous NEWS2", group="baseline"),
    "openloop_ml": dict(label="Open-loop ML (cloud)", group="baseline",
                        kw=dict(fault_dx=False, validation=False, adapt=False, edge=False, adaptive_sampling=False)),
    "veriloop": dict(label="VeriLoop (full)", group="proposed", kw={}),
    "no_faultdx": dict(label="w/o fault diagnosis & data confirmation", group="ablation", kw=dict(fault_dx=False)),
    "no_verify": dict(label="w/o outcome verification", group="ablation", kw=dict(validation=False)),
    "no_adapt": dict(label="w/o rule adaptation", group="ablation", kw=dict(adapt=False)),
    "cloud": dict(label="cloud-hosted loop (no edge)", group="ablation", kw=dict(edge=False)),
    "no_adsamp": dict(label="w/o adaptive sampling", group="ablation", kw=dict(adaptive_sampling=False)),
}


def make_policy(cfg, pt, model):
    if cfg == "spot_news2":
        return SpotNEWS2(pt.copd_scale2, offset=pt.pid % 48)
    if cfg == "cont_news2":
        return ContNEWS2(pt.copd_scale2)
    return VeriLoop(pt.copd_scale2, model, name=cfg, **CONFIGS[cfg]["kw"])


def run_one(args):
    cfg, seed, pid, model, days = args
    pt = make_patient(pid, np.random.default_rng([seed, pid]), days)
    env = Env(pt, np.random.default_rng([seed, pid, 7]))
    pol = make_policy(cfg, pt, model)
    planned = [(f.kind, f.end - f.start) for f in pt.faults]
    s = env.run(pol)
    # Hours of corrupted (non-spike) data actually experienced vs. planned fault life.
    s["fault_hours"] = sum((f.end - f.start) / 12 for f in pt.faults if f.kind != "spike")
    s["fault_hours_planned"] = sum(d / 12 for k, d in planned if k != "spike")
    ft, ff, sm = s.pop("fault_truth"), s.pop("fault_flag"), s.pop("sampled")
    m = sm[:, None]
    s["fault_tp"] = int((ft & ff & m).sum())
    s["fault_fp"] = int((~ft & ff & m).sum())
    s["fault_fn"] = int((ft & ~ff & m).sum())
    lat = np.array(s.pop("latency_ms"))
    s["lat_p50"] = float(np.percentile(lat, 50)) if len(lat) else np.nan
    s["lat_p99"] = float(np.percentile(lat, 99)) if len(lat) else np.nan
    s["lat_over_1min"] = float((lat > 60e3).mean()) if len(lat) else np.nan
    s["lat_all"] = lat[lat < 60e3][::7].round(2).tolist()    # thinned, for the CDF figure
    s["lat_tail"] = lat[lat >= 60e3].round(0).tolist()
    if isinstance(pol, VeriLoop):
        s["ledger_records"] = pol.ledger.n
        s["ledger_bytes"] = pol.ledger.bytes
        s["theta_final"] = pol.theta
        s["amber_final"] = pol.amber_thr
    s["cfg"], s["seed"] = cfg, seed
    s["eval_days"] = (env.T - STEPS_PER_DAY) / STEPS_PER_DAY
    return s
