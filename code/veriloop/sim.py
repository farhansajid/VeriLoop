"""Discrete-event closed-loop simulator.

The environment owns the ground truth (deterioration progress, sensor
faults, connectivity) and the human actors (patient, clinician).  A
monitoring policy only sees what a real deployment would see: noisy,
possibly faulty observations, delayed by outages when it runs in the
cloud, and the answers to the requests it makes.
"""
from __future__ import annotations

import time

import numpy as np

from .physio import N_CH, STEPS_PER_DAY, Patient, quantise

TELECONSULT, ESCALATE, REMEASURE, DEVICE_CHECK = "teleconsult", "escalate", "remeasure", "device_check"
TREAT_ONSET = 6          # 30 min from clinician response to physiological effect
TREAT_TAU = 24           # 2 h decay constant of effective treatment
RECOGNISE_P = 0.25       # clinician recognises deterioration above this progress
SPOT_NOISE = np.array([2.0, 1.0, 0.7, 4.0, 0.05])


class Env:
    def __init__(self, patient: Patient, rng: np.random.Generator):
        self.pt = patient
        self.rng = rng
        self.T = patient.base.shape[0]
        self.p = 0.0
        self.ep_idx = -1
        self.ep_active = False
        self.decay_from = None          # step after which an effective treatment acts
        self.events = []                # (due_step, event_dict)
        self.pending_clin = {}          # tier -> due step (de-duplicates requests)
        self.last_obs = np.full(N_CH, np.nan)
        self.ep_log = [dict(onset=e.onset, ramp=e.ramp, phenotype=e.phenotype, critical=False,
                            resolved=False, first_alert=None, first_effective=None, t_end=None,
                            alerts_during=0, treatments=0) for e in patient.episodes]
        self.alerts = []                # dicts: t, tier, true, fault_recent
        self.counts = dict(remeasure=0, device_check=0, teleconsult=0, escalate=0, sampled=0,
                           uplink_bytes=0, uplink_msgs=0)
        self.fault_truth = np.zeros((self.T, N_CH), bool)
        self.fault_flag = np.zeros((self.T, N_CH), bool)
        self.latency_ms = []
        self.sampled = np.zeros(self.T, bool)

    # ----------------------------------------------------------- physiology
    def _advance_physiology(self, t):
        pt = self.pt
        if not self.ep_active and self.ep_idx + 1 < len(pt.episodes) and t >= pt.episodes[self.ep_idx + 1].onset:
            self.ep_idx += 1
            self.ep_active, self.p, self.decay_from = True, 0.0, None
        if not self.ep_active:
            return
        ep = pt.episodes[self.ep_idx]
        log = self.ep_log[self.ep_idx]
        if self.decay_from is not None and t >= self.decay_from:
            self.p *= np.exp(-1.0 / TREAT_TAU)
            if self.p < 0.05:
                log["resolved"], log["t_end"] = True, t
                self.ep_active = False
                self.p = 0.0
        else:
            self.p += 1.0 / ep.ramp
            if self.p >= 1.0:
                log["critical"], log["t_end"] = True, t
                self.ep_active = False      # admitted to hospital; episode ends
                self.p = 0.0

    def true_vitals(self, t):
        x = self.pt.base[t].copy()
        if self.ep_active:
            x += (self.p ** 1.5) * self.pt.episodes[self.ep_idx].delta
        return x

    def observe(self, t):
        x = self.true_vitals(t) + self.rng.normal(0, 0.3, N_CH) * np.array([1, 0.5, 0.3, 1, 0.02])
        for f in self.pt.faults:
            if not (f.start <= t < f.end):
                continue
            chs = range(N_CH) if f.ch < 0 else [f.ch]
            for c in chs:
                self.fault_truth[t, c] = f.kind != "dropout"
            if f.kind == "spike":
                x[f.ch] += f.mag
            elif f.kind == "dropout":
                x[:] = np.nan
            elif f.kind == "stuck":
                if np.isnan(f.value):
                    f.value = self.last_obs[f.ch] if not np.isnan(self.last_obs[f.ch]) else x[f.ch]
                x[f.ch] = f.value
            elif f.kind == "drift":
                ramp = min(1.0, (t - f.start) / 12)
                x[f.ch] += ramp * f.mag
            elif f.kind == "detached":
                x[f.ch] = f.value
        x[2] = min(x[2], 100.0)
        x = quantise(x)
        ok = ~np.isnan(x)
        self.last_obs[ok] = x[ok]
        return x

    def fault_active_recent(self, t, window=6):
        lo = max(0, t - window)
        return bool(self.fault_truth[lo:t + 1].any()) or any(
            f.kind == "dropout" and f.start <= t < f.end for f in self.pt.faults)

    # ------------------------------------------------------------- actors
    def request(self, t, action):
        kind = action["type"]
        if kind in (TELECONSULT, ESCALATE):
            tier = 2 if kind == TELECONSULT else 3
            # A pending request of the same or higher tier absorbs this one.
            if any(k >= tier and due > t for k, due in self.pending_clin.items()):
                return
            self.counts[kind] += 1
            rec = dict(t=t, tier=tier, true=self.ep_active, fault_recent=self.fault_active_recent(t),
                       src=action.get("src", ""))
            self.alerts.append(rec)
            if self.ep_active:
                self.ep_log[self.ep_idx]["alerts_during"] += 1
                if self.ep_log[self.ep_idx]["first_alert"] is None:
                    self.ep_log[self.ep_idx]["first_alert"] = t
            med = 4 if tier == 2 else 2
            delay = max(1, int(round(self.rng.lognormal(np.log(med), 0.5))))
            self.pending_clin[tier] = t + delay
            self.events.append((t + delay, dict(type="clinician", tier=tier, req_t=t, req_id=action.get("id"))))
        elif kind == REMEASURE:
            self.counts["remeasure"] += 1
            if self.rng.random() < 0.85:
                self.events.append((t + 2, dict(type="remeasure", req_id=action.get("id"))))
            else:
                self.events.append((t + 4, dict(type="remeasure_none", req_id=action.get("id"))))
        elif kind == DEVICE_CHECK:
            self.counts["device_check"] += 1
            if self.rng.random() < 0.8:
                self.events.append((t + int(self.rng.integers(3, 7)), dict(type="device_fixed")))

    def _resolve_event(self, t, ev):
        """Turn a due event into the feedback the policy receives."""
        if ev["type"] == "clinician":
            self.pending_clin.pop(ev["tier"], None)
            if self.ep_active and self.p >= RECOGNISE_P:
                log = self.ep_log[self.ep_idx]
                log["treatments"] += 1
                base_eff = 0.75 if ev["tier"] == 2 else 0.9
                damp = 0.4 if ev["tier"] == 2 else 0.3
                effective = self.rng.random() < base_eff * (1 - damp * self.p)
                if effective and self.decay_from is None:
                    self.decay_from = t + TREAT_ONSET
                    if log["first_effective"] is None:
                        log["first_effective"] = t
                return dict(type="clinician", tier=ev["tier"], finding="treated", req_t=ev["req_t"], req_id=ev["req_id"])
            finding = "early_unconfirmed" if self.ep_active else "false_alarm"
            return dict(type="clinician", tier=ev["tier"], finding=finding, req_t=ev["req_t"], req_id=ev["req_id"])
        if ev["type"] == "remeasure":
            x = quantise(self.true_vitals(t) + self.rng.normal(0, 1, N_CH) * SPOT_NOISE)
            return dict(type="remeasure", values=x, req_id=ev["req_id"])
        if ev["type"] == "device_fixed":
            for f in self.pt.faults:
                if f.kind != "spike" and f.start <= t < f.end:
                    f.end = t
            return dict(type="device_fixed")
        return ev

    # ---------------------------------------------------------------- run
    def run(self, policy):
        buffer = []
        for t in range(self.T):
            self._advance_physiology(t)
            feedback = []
            due = [e for e in self.events if e[0] <= t]
            self.events = [e for e in self.events if e[0] > t]
            for _, ev in due:
                feedback.append(self._resolve_event(t, ev))

            x = self.observe(t)          # sensor state evolves even if not sampled
            sampled = policy.wants_sample(t)
            if sampled:
                self.counts["sampled"] += 1
                self.sampled[t] = True
            obs = x if sampled else None

            actions = []
            for fb in feedback:
                actions += policy.feedback(t, fb)

            if policy.edge or not self.pt.outage[t]:
                pending = buffer + [(t, obs)]
                buffer = []
                for t_obs, o in pending:
                    t0 = time.perf_counter()
                    acts = policy.observe(t, t_obs, o)
                    comp = (time.perf_counter() - t0) * 1e3
                    if o is not None:
                        wan = 0.0 if policy.edge else float(self.rng.lognormal(np.log(80), 0.6))
                        local = 20.0                       # BLE hop to the gateway
                        self.latency_ms.append(comp + local + wan + (t - t_obs) * 5 * 60e3)
                    actions += acts
            else:
                buffer.append((t, obs))

            flags = getattr(policy, "current_flags", None)
            if flags is not None:
                self.fault_flag[t] = flags
            for a in actions:
                self.request(t, a)
        self.counts["uplink_bytes"] = getattr(policy, "uplink_bytes", 0)
        self.counts["uplink_msgs"] = getattr(policy, "uplink_msgs", 0)
        return self.summary()

    def summary(self):
        days = self.T / STEPS_PER_DAY
        # The calibration day is excluded from alarm statistics for every method.
        alerts = [a for a in self.alerts if a["t"] >= STEPS_PER_DAY]
        return dict(pid=self.pt.pid, group=self.pt.group, days=days, episodes=self.ep_log, alerts=alerts,
                    counts=self.counts, latency_ms=self.latency_ms,
                    fault_truth=self.fault_truth[STEPS_PER_DAY:], fault_flag=self.fault_flag[STEPS_PER_DAY:],
                    sampled=self.sampled[STEPS_PER_DAY:])
