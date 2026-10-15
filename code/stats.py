"""Paired seed-level differences and pooled Fisher tests used in the text."""
import json
import pickle
from pathlib import Path

import numpy as np
from scipy.stats import fisher_exact

res = Path("../results")
P = json.load(open(res / "per_seed_metrics.json"))
R = pickle.load(open(res / "runs.pkl", "rb"))


def get(c, k):
    return np.array([p[k] for p in sorted([p for p in P if p["cfg"] == c], key=lambda x: x["seed"])])


def counts(c):
    eps = [e for r in R if r["cfg"] == c for e in r["episodes"]]
    return int(sum(e["critical"] for e in eps)), len(eps)


out = {"adverse_counts": {c: counts(c) for c in sorted({r["cfg"] for r in R})}, "paired": {}, "fisher": {}}
for b in ["spot_news2", "cont_news2", "openloop_ml", "no_faultdx", "no_verify", "no_adapt", "cloud", "no_adsamp"]:
    out["paired"][b] = {}
    for k in ["adverse_pct", "false_alarms_per_pd", "clinician_per_pd", "lead_h_median"]:
        d = get("veriloop", k) - get(b, k)
        out["paired"][b][k] = [float(d.mean()), float(1.96 * d.std(ddof=1) / np.sqrt(len(d)))]
    a1, n1 = counts("veriloop")
    b1, n2 = counts(b)
    odds, p = fisher_exact([[a1, n1 - a1], [b1, n2 - b1]])
    out["fisher"][b] = dict(odds_ratio=float(odds), p=float(p))
json.dump(out, open(res / "stats.json", "w"), indent=1)
print(json.dumps(out["fisher"], indent=1))
