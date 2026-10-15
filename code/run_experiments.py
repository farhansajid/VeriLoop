"""Train the risk model, run all configurations on 5 seeds and save results.

Usage:  python run_experiments.py [--patients 150] [--seeds 5] [--workers 4]
"""
import argparse
import json
import pickle
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from veriloop.experiments import CONFIGS, run_one
from veriloop.metrics import mean_ci, per_seed
from veriloop.train import train

ap = argparse.ArgumentParser()
ap.add_argument("--patients", type=int, default=150)
ap.add_argument("--seeds", type=int, default=5)
ap.add_argument("--days", type=int, default=7)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--out", default="../results")
a = ap.parse_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)

t0 = time.time()
model, stats = train(seed=1000, n_patients=a.patients)
print("risk model:", stats, flush=True)
json.dump(stats, open(out / "risk_model.json", "w"), indent=2)
pickle.dump(model, open(out / "risk_model.pkl", "wb"))

jobs = [(c, s, p, model, a.days) for c in CONFIGS for s in range(a.seeds) for p in range(a.patients)]
with Pool(a.workers) as pool:
    res = pool.map(run_one, jobs, chunksize=8)
print(f"simulated {len(res)} patient-weeks in {time.time() - t0:.0f} s", flush=True)

lat = {c: dict(body=[x for r in res if r["cfg"] == c and r["seed"] == 0 for x in r["lat_all"]],
               tail=[x for r in res if r["cfg"] == c and r["seed"] == 0 for x in r["lat_tail"]],
               n=sum(len(r["lat_all"]) * 7 + len(r["lat_tail"]) for r in res if r["cfg"] == c and r["seed"] == 0))
       for c in CONFIGS}
json.dump(lat, open(out / "latency_samples.json", "w"))
for r in res:
    r.pop("lat_all"), r.pop("lat_tail")
pickle.dump(res, open(out / "runs.pkl", "wb"))

seed_rows, summary = [], {}
for c in CONFIGS:
    per = []
    for s in range(a.seeds):
        m = per_seed([r for r in res if r["cfg"] == c and r["seed"] == s])
        m.update(cfg=c, seed=s)
        per.append(m)
    seed_rows += per
    keys = [k for k in per[0] if k not in ("cfg", "seed")]
    summary[c] = {k: mean_ci([p[k] for p in per]) for k in keys}
    summary[c]["label"] = CONFIGS[c]["label"]
json.dump(seed_rows, open(out / "per_seed_metrics.json", "w"), indent=1)
json.dump(summary, open(out / "summary.json", "w"), indent=1)
for c in CONFIGS:
    s = summary[c]
    print(f"{c:12s} adverse {s['adverse_pct'][0]:5.1f}±{s['adverse_pct'][1]:.1f}  lead {s['lead_h_median'][0]:.2f}h  "
          f"FA/pd {s['false_alarms_per_pd'][0]:.2f}  clin/pd {s['clinician_per_pd'][0]:.2f}  "
          f"duty {s['duty_pct'][0]:.0f}%  up {s['uplink_kb_per_pd'][0]:.1f}KB")
print(f"done in {time.time() - t0:.0f} s")
