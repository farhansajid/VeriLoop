"""Aggregate per-patient run summaries into the paper's metrics."""
from __future__ import annotations

import numpy as np

from .physio import STEPS_PER_DAY

H = 60 / 5   # steps per hour


def per_seed(rows):
    """rows: summaries of one configuration and one seed."""
    eps = [e for r in rows for e in r["episodes"]]
    days = sum(r["eval_days"] for r in rows)
    alerts = [a for r in rows for a in r["alerts"]]
    fa = [a for a in alerts if not a["true"]]
    detected = [e for e in eps if e["first_alert"] is not None]
    lead = [(e["onset"] + e["ramp"] - e["first_alert"]) / H for e in detected]
    tp = sum(r["fault_tp"] for r in rows)
    fp = sum(r["fault_fp"] for r in rows)
    fn = sum(r["fault_fn"] for r in rows)
    T_steps = sum(r["days"] for r in rows) * STEPS_PER_DAY
    out = dict(
        n_patients=len(rows), patient_days=days, n_episodes=len(eps),
        adverse_pct=100 * np.mean([e["critical"] for e in eps]) if eps else np.nan,
        sensitivity_pct=100 * len(detected) / len(eps) if eps else np.nan,
        lead_h_median=float(np.median(lead)) if lead else np.nan,
        false_alarms_per_pd=len(fa) / days,
        fault_fa_per_pd=sum(a["fault_recent"] for a in fa) / days,
        ppv_pct=100 * (len(alerts) - len(fa)) / len(alerts) if alerts else np.nan,
        clinician_per_pd=sum(r["counts"]["teleconsult"] + r["counts"]["escalate"] for r in rows) / days,
        escalations_per_pd=sum(r["counts"]["escalate"] for r in rows) / days,
        remeasure_per_pd=sum(r["counts"]["remeasure"] for r in rows) / days,
        device_check_per_pd=sum(r["counts"]["device_check"] for r in rows) / days,
        verify_escalations_per_pd=sum(a.get("src") == "verify" for a in alerts) / days,
        verify_escalations_true_pct=100 * np.mean([a["true"] for a in alerts if a.get("src") == "verify"])
        if any(a.get("src") == "verify" for a in alerts) else np.nan,
        treatments_per_episode=float(np.mean([e["treatments"] for e in eps])) if eps else np.nan,
        fault_hours_per_pd=sum(r["fault_hours"] for r in rows) / sum(r["days"] for r in rows),
        patient_req_per_pd=sum(r["counts"]["remeasure"] + r["counts"]["device_check"] for r in rows) / days,
        duty_pct=100 * sum(r["counts"]["sampled"] for r in rows) / T_steps,
        uplink_kb_per_pd=sum(r["counts"]["uplink_bytes"] for r in rows) / 1024 / sum(r["days"] for r in rows),
        lat_p50_ms=float(np.nanmedian([r["lat_p50"] for r in rows])),
        lat_p99_ms=float(np.nanmedian([r["lat_p99"] for r in rows])),
        lat_over_1min_pct=100 * float(np.nanmean([r["lat_over_1min"] for r in rows])),
        fault_precision_pct=100 * tp / (tp + fp) if tp + fp else np.nan,
        fault_recall_pct=100 * tp / (tp + fn) if tp + fn else np.nan,
    )
    if "ledger_records" in rows[0]:
        d = sum(r["days"] for r in rows)
        out["ledger_records_per_pd"] = sum(r["ledger_records"] for r in rows) / d
        out["ledger_kb_per_pd"] = sum(r["ledger_bytes"] for r in rows) / 1024 / d
        out["theta_final_mean"] = float(np.mean([r["theta_final"] for r in rows]))
    return out


def mean_ci(vals):
    v = np.array([x for x in vals if not np.isnan(x)])
    if len(v) == 0:
        return np.nan, np.nan
    m = v.mean()
    half = 1.96 * v.std(ddof=1) / np.sqrt(len(v)) if len(v) > 1 else 0.0
    return float(m), float(half)
