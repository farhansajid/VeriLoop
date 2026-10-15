"""Build Fig. 3 and the two LaTeX tables from results/summary.json."""
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Embed TrueType (Type 42) fonts: IEEE PDF checks reject Type 3 fonts.
plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42, "font.family": "DejaVu Sans"})

res = Path("../results")
fig_dir = Path("../paper/figures")
S = json.load(open(res / "summary.json"))
L = json.load(open(res / "latency_samples.json"))

SHORT = {"spot_news2": "Spot NEWS2", "cont_news2": "Cont. NEWS2", "openloop_ml": "Open-loop ML",
         "veriloop": "VeriLoop", "no_faultdx": "–fault dx", "no_verify": "–verify", "no_adapt": "–adapt",
         "cloud": "cloud-hosted", "no_adsamp": "–adaptive sampling"}
COL = {"baseline": "#7f7f7f", "proposed": "#d62728", "ablation": "#1f77b4"}
GROUP = {"spot_news2": "baseline", "cont_news2": "baseline", "openloop_ml": "baseline", "veriloop": "proposed"}

fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.9))
a = ax[0]
SHOW = ["spot_news2", "cont_news2", "openloop_ml", "veriloop", "no_verify", "no_faultdx"]
OFF = {"spot_news2": (8, -4), "cont_news2": (-25, 10), "openloop_ml": (-25, 10), "veriloop": (-30, 14),
       "no_verify": (-62, -3), "no_faultdx": (-14, -14)}
for c in SHOW:
    s = S[c]
    g = GROUP.get(c, "ablation")
    x, xe = s["clinician_per_pd"]
    y, ye = s["adverse_pct"]
    a.errorbar(x, y, xerr=xe, yerr=ye, fmt="*" if g == "proposed" else "o", ms=11 if g == "proposed" else 6,
               color=COL[g], capsize=2, lw=0.8, zorder=3)
    a.annotate(SHORT[c], (x, y), xytext=OFF[c], textcoords="offset points", fontsize=7.5, color=COL[g])
a.set_xlabel("clinician contacts per patient-day")
a.set_ylabel("episodes reaching critical state (%)")
a.set_title("(a) safety vs. clinical workload", fontsize=9)
a.set_xlim(0.3, 2.5)
a.set_ylim(-7, 72)
a.grid(alpha=0.3)

b = ax[1]
for c, col, ls in [("veriloop", "#d62728", "-"), ("cloud", "#1f77b4", "--"), ("cont_news2", "#7f7f7f", ":")]:
    body = np.array(L[c]["body"])
    tail = np.array(L[c]["tail"])
    # body was thinned 1:7 when saved; re-weight it so the CDF is correct.
    vals = np.concatenate([np.repeat(body, 7), tail])
    vals.sort()
    p = np.arange(1, len(vals) + 1) / len(vals)
    b.plot(vals / 1000, p, color=col, ls=ls, lw=1.4, label=SHORT[c])
b.set_xscale("log")
b.set_xlim(0.01, 1e4)
b.set_ylim(0.95, 1.0005)
b.set_xlabel("sense-to-decision latency (s)")
b.set_ylabel("CDF (upper 5%)")
b.set_title("(b) loop latency incl. connectivity outages", fontsize=9)
b.axvline(300, color="black", lw=0.6, ls="-.")
b.text(250, 0.9515, "one sampling period ", fontsize=6.5, ha="right")
b.legend(fontsize=7, loc="lower center", bbox_to_anchor=(0.52, 0.1), frameon=False)
b.grid(alpha=0.3, which="both")
fig.tight_layout()
fig.savefig(fig_dir / "results.pdf")
fig.savefig(res / "results.png", dpi=160)


def f(c, k, d=1, ci=True):
    m, h = S[c][k]
    if m is None or np.isnan(m):
        return "--"
    return f"{m:.{d}f}" + (f"\\,$\\pm$\\,{h:.{d}f}" if ci else "")


rows_main = ["spot_news2", "cont_news2", "openloop_ml", "veriloop"]
lines = []
for c in rows_main:
    name = S[c]["label"].replace("&", "\\&")
    if c == "veriloop":
        name = "\\textbf{VeriLoop}"
    lines.append(" & ".join([name, f(c, "adverse_pct"), f(c, "sensitivity_pct", 1, False), f(c, "lead_h_median", 2, False),
                             f(c, "false_alarms_per_pd", 2), f(c, "ppv_pct", 1, False), f(c, "clinician_per_pd", 2),
                             f(c, "patient_req_per_pd", 2, False)]) + " \\\\")
open(fig_dir / "table_main.tex", "w").write("\n".join(lines) + "\n")

rows_ab = ["veriloop", "no_faultdx", "no_verify", "no_adapt", "cloud", "no_adsamp", "openloop_ml", "cont_news2"]
lines = []
for c in rows_ab:
    name = S[c]["label"].replace("&", "\\&")
    if c == "veriloop":
        name = "\\textbf{VeriLoop (full)}"
    lines.append(" & ".join([name, f(c, "adverse_pct"), f(c, "false_alarms_per_pd", 2, False), f(c, "fault_fa_per_pd", 2, False),
                             f(c, "fault_hours_per_pd", 2, False), f(c, "duty_pct", 0, False), f(c, "uplink_kb_per_pd", 1, False),
                             f(c, "lat_over_1min_pct", 2, False)]) + " \\\\")
open(fig_dir / "table_ablation.tex", "w").write("\n".join(lines) + "\n")
print(open(fig_dir / "table_main.tex").read())
print(open(fig_dir / "table_ablation.tex").read())

# Combined table (Table I in the paper): all designs and ablations in one table.
GROUPS = [("Reference designs", ["spot_news2", "cont_news2", "openloop_ml"]),
          ("Proposed", ["veriloop"]),
          ("VeriLoop ablations (one stage removed)", ["no_faultdx", "no_verify", "no_adapt", "cloud", "no_adsamp"])]
NAMES = {"no_faultdx": "$-$ fault diagnosis \\& confirmation", "no_verify": "$-$ outcome verification",
         "no_adapt": "$-$ rule adaptation", "cloud": "$-$ edge (cloud-hosted loop)", "no_adsamp": "$-$ adaptive sampling",
         "veriloop": "\\textbf{VeriLoop}"}
lines = []
for title, cfgs in GROUPS:
    lines.append(f"\\multicolumn{{11}}{{l}}{{\\textit{{{title}}}}} \\\\")
    for c in cfgs:
        name = NAMES.get(c, S[c]["label"])
        lines.append(" & ".join([name, f(c, "adverse_pct"), f(c, "lead_h_median", 1, False), f(c, "false_alarms_per_pd", 2, False),
                                 f(c, "ppv_pct", 0, False), f(c, "clinician_per_pd", 2, False), f(c, "patient_req_per_pd", 1, False),
                                 f(c, "fault_hours_per_pd", 1, False), f(c, "duty_pct", 0, False), f(c, "uplink_kb_per_pd", 1, False),
                                 f(c, "lat_over_1min_pct", 1, False)]) + " \\\\")
    lines.append("\\midrule")
lines.pop()
open(fig_dir / "table_combined.tex", "w").write("\n".join(lines) + "\n")
print(open(fig_dir / "table_combined.tex").read())
