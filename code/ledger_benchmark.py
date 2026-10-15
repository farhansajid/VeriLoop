"""Provenance-ledger overhead and tamper-evidence test.

Runs VeriLoop on one virtual patient-week while keeping the full hash
chain, then (i) times append/verify, (ii) applies 4 x 250 random
tampering attacks and checks that verification against the cloud-held
hourly anchors detects each of them.
"""
import json
import pickle
import random
import time
from pathlib import Path

import numpy as np

from veriloop.physio import make_patient
from veriloop.policies import VeriLoop, verify_chain
from veriloop.sim import Env

out = Path("../results")
model = pickle.load(open(out / "risk_model.pkl", "rb"))
pt = make_patient(3, np.random.default_rng([0, 3]), 7)
pol = VeriLoop(pt.copd_scale2, model, keep_ledger=True)
Env(pt, np.random.default_rng([0, 3, 7])).run(pol)
recs, anchors = pol.ledger.records, pol.ledger.anchors

# Append cost on its own (re-hashing the same bodies).
import hashlib
t0 = time.perf_counter()
prev = "0" * 64
for body, _ in recs:
    prev = hashlib.sha256((prev + body).encode()).hexdigest()
append_us = (time.perf_counter() - t0) / len(recs) * 1e6
t0 = time.perf_counter()
assert verify_chain(recs, anchors)
verify_ms = (time.perf_counter() - t0) * 1e3

rnd = random.Random(0)
attacks = {}
for kind in ["modify", "delete", "reorder", "truncate"]:
    hit = 0
    n = 250
    for _ in range(n):
        r = list(recs)
        if kind == "modify":
            i = rnd.randrange(len(r))
            body = json.loads(r[i][0])
            body["r"] = round(rnd.random(), 3) if body.get("r") != 0.5 else 0.1
            body["tier"] = 3 if body.get("tier") != 3 else 0
            r[i] = (json.dumps(body, sort_keys=True, separators=(",", ":")), r[i][1])
        elif kind == "delete":
            del r[rnd.randrange(1, len(r))]
        elif kind == "reorder":
            i = rnd.randrange(len(r) - 1)
            r[i], r[i + 1] = r[i + 1], r[i]
        else:   # drop the tail of the log, which a plain hash chain cannot detect without anchors
            r = r[: rnd.randrange(len(r) - 300, len(r) - 20)]
        hit += not verify_chain(r, anchors)
    attacks[kind] = 100 * hit / n

res = dict(records=len(recs), anchors=len(anchors), bytes_per_record=pol.ledger.bytes / len(recs),
           append_us_per_record=append_us, verify_ms_full_week=verify_ms, detection_pct=attacks)
json.dump(res, open(out / "ledger_benchmark.json", "w"), indent=2)
print(res)
