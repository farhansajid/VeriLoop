"""Find and plot one illustrative loop trace (Fig. 2): a deterioration
whose first treatment fails and is caught by outcome verification, with
a sensor fault handled along the way.  The same patient is replayed under
continuous NEWS2 for comparison."""
import pickle
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Embed TrueType (Type 42) fonts: IEEE PDF checks reject Type 3 fonts.
plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42, "font.family": "DejaVu Sans"})

from veriloop.physio import make_patient
from veriloop.policies import ContNEWS2, VeriLoop
from veriloop.sim import Env

out = Path("../results")
model = pickle.load(open(out / "risk_model.pkl", "rb"))


class Traced(VeriLoop):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.trace, self.acts, self.fbs = [], [], []

    def observe(self, t, t_obs, obs):
        acts = super().observe(t, t_obs, obs)
        self.trace.append((t, None if obs is None else obs.copy(), self.r, self.current_flags.copy()))
        self.acts += [(t, a["type"], a["src"]) for a in acts]
        return acts

    def feedback(self, t, fb):
        acts = super().feedback(t, fb)
        self.fbs.append((t, fb["type"], fb.get("finding", "")))
        self.acts += [(t, a["type"], a["src"]) for a in acts]
        return acts


def run(seed, pid):
    pt = make_patient(pid, np.random.default_rng([seed, pid]), 7)
    env = Env(pt, np.random.default_rng([seed, pid, 7]))
    pol = Traced(pt.copd_scale2, model)
    s = env.run(pol)
    return pt, env, pol, s


best = None
for seed in range(3):
    for pid in range(150):
        pt = make_patient(pid, np.random.default_rng([seed, pid]), 7)
        if not pt.episodes:
            continue
        pt, env, pol, s = run(seed, pid)
        pt_b = make_patient(pid, np.random.default_rng([seed, pid]), 7)
        s_b = Env(pt_b, np.random.default_rng([seed, pid, 7])).run(ContNEWS2(pt_b.copd_scale2))
        for k, e in enumerate(s["episodes"]):
            ver = [a for a in pol.acts if a[2] == "verify" and e["onset"] <= a[0] <= (e["first_effective"] or -1)]
            flt = [f for f in pt.faults if f.kind != "spike" and e["onset"] - 24 <= f.start <= e["t_end"]]
            if ver and e["resolved"] and s_b["episodes"][k]["critical"] and flt:
                best = (seed, pid, k)
                break
        if best:
            break
    if best:
        break
print("case:", best)
seed, pid, k = best
pt, env, pol, s = run(seed, pid)
e = s["episodes"][k]
pt2 = make_patient(pid, np.random.default_rng([seed, pid]), 7)
env2 = Env(pt2, np.random.default_rng([seed, pid, 7]))
s2 = env2.run(ContNEWS2(pt2.copd_scale2))
e2 = s2["episodes"][k]
print("VeriLoop episode:", e)
print("Cont NEWS2 episode:", e2)

lo, hi = e["onset"] - 24, (e["t_end"] or e["onset"] + e["ramp"]) + 18
hrs = lambda t: (np.asarray(t) - e["onset"]) / 12
tr = [x for x in pol.trace if lo <= x[0] <= hi]
T = np.array([x[0] for x in tr])
O = np.array([x[1] if x[1] is not None else np.full(5, np.nan) for x in tr], float)
R = np.array([x[2] for x in tr])
ok = ~np.isnan(O).all(axis=1)
# Plot sampled points only, so that gaps show missing data.
Ts, Os = T[ok], O[ok]
gaps = np.where(np.diff(Ts) > 3)[0]
Ts = np.insert(Ts.astype(float), gaps + 1, np.nan)
Os = np.insert(Os, gaps + 1, np.nan, axis=0)

fig, ax = plt.subplots(3, 1, figsize=(3.5, 4.6), sharex=True, gridspec_kw=dict(height_ratios=[1.1, 1.1, 1]))
ax[0].plot(hrs(Ts), Os[:, 0], color="#1f77b4", lw=1.2, label="HR (bpm)")
ax[0].plot(hrs(Ts), Os[:, 1], color="#9467bd", lw=1.2, label="RR (/min)")
ax[0].set_ylabel("HR, RR")
ax[1].plot(hrs(Ts), Os[:, 3], color="#d62728", lw=1.2, label="SBP (mmHg)")
ax1b = ax[1].twinx()
ax1b.plot(hrs(Ts), Os[:, 2], color="#2ca02c", lw=1.2, label="SpO$_2$ (%)")
ax1b.set_ylabel("SpO$_2$ (%)", color="#2ca02c")
ax[1].set_ylabel("SBP (mmHg)", color="#d62728")
ax[2].plot(hrs(T), R, color="black", lw=1.2, label="VeriLoop risk")
ax[2].axhline(0.5, color="grey", ls=":", lw=0.8)
ax[2].set_ylabel("risk")
ax[2].set_ylim(-0.02, 1.25)
ax[2].set_xlabel("hours from (hidden) deterioration onset")

# wearable dropouts
miss = ~ok
for a in ax:
    i = 0
    while i < len(T):
        if miss[i] and (i == 0 or not miss[i - 1]):
            j = i
            while j + 1 < len(T) and miss[j + 1]:
                j += 1
            if j - i >= 3:
                a.axvspan(hrs(T[i]), hrs(T[j]), color="grey", alpha=0.15, lw=0)
            i = j
        i += 1

marks = {("remeasure", "confirm"): ("v", "#ff7f0e", "re-measure request", 1.12),
         ("escalate", "loop"): ("s", "#d62728", "escalation", 1.12),
         ("teleconsult", "loop"): ("o", "#1f77b4", "teleconsult", 1.12),
         ("escalate", "verify"): ("*", "#d62728", "escalation after failed verification", 1.12),
         ("device_check", "suspect"): ("D", "#7f7f7f", "device check", 1.12),
         ("device_check", "missing"): ("D", "#7f7f7f", "device check", 1.12),
         ("device_check", "mismatch"): ("D", "#7f7f7f", "device check", 1.12)}
seen = set()
# Clinician requests are taken from the environment, which drops duplicates of a pending request.
counted = [(a["t"], "teleconsult" if a["tier"] == 2 else "escalate", a["src"]) for a in env.alerts]
shown = [a for a in pol.acts if a[1] not in ("teleconsult", "escalate")] + counted
for t, typ, src in sorted(shown):
    if lo <= t <= hi and (typ, src) in marks:
        m, c, l, y = marks[(typ, src)]
        ax[2].plot(hrs(t), y, marker=m, color=c, ms=10 if m == "*" else 6, ls="none", zorder=5 if m == "*" else 3,
                   label=None if l in seen else l)
        seen.add(l)
first = True
for t, typ, f in pol.fbs:
    if lo <= t <= hi and typ == "clinician" and f == "treated":
        for a in ax:
            a.axvline(hrs(t), color="#2ca02c", lw=0.8, ls="--")
        ax[2].plot([], [], color="#2ca02c", lw=0.8, ls="--", label="treatment started" if first else None)
        first = False
crit = hrs(e["onset"] + e["ramp"])
for a in ax:
    a.axvline(crit, color="black", lw=0.8, ls="-.")
ax[0].text(crit, ax[0].get_ylim()[1], " untreated\n critical", va="top", fontsize=6)
if e2["first_alert"] is not None:
    ax[2].plot(hrs(e2["first_alert"]), 0.8, marker="^", color="#8c564b", ls="none", ms=6,
               label="continuous NEWS2: first alert")
if e2["critical"]:
    ax[2].plot(hrs(e2["t_end"]), 0.8, marker="X", color="#8c564b", ls="none", ms=7,
               label="continuous NEWS2: critical")
ax[2].plot([], [], color="grey", alpha=0.3, lw=6, label="wearable data missing")
h, l = ax[2].get_legend_handles_labels()
fig.legend(h, l, fontsize=5.6, ncol=2, loc="lower center", frameon=False, bbox_to_anchor=(0.5, 0.0))
ax[0].legend(fontsize=6, loc="center left", frameon=False)
for a in list(ax) + [ax1b]:
    a.tick_params(labelsize=6.5)
    a.yaxis.label.set_size(7)
ax[2].xaxis.label.set_size(7)
fig.align_ylabels()
fig.tight_layout(h_pad=0.3, rect=(0, 0.17, 1, 1))
fig.savefig("../paper/figures/case_trace.pdf")
fig.savefig(out / "case_trace.png", dpi=160)
open(out / "case_study.txt", "w").write(f"seed={seed} pid={pid} group={pt.group} phenotype={pt.episodes[k].phenotype}\n"
                                        f"VeriLoop: {e}\nContNEWS2: {e2}\nactions: {[a for a in pol.acts if lo <= a[0] <= hi]}\n"
                                        f"feedback: {[f for f in pol.fbs if lo <= f[0] <= hi]}\n")
