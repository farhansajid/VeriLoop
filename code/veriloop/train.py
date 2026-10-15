"""Train the fog-tier deterioration-risk model on a separate cohort.

The training cohort is simulated without any monitoring intervention
(natural history), passed through the same edge cleaning and feature
pipeline that VeriLoop uses online, and labelled with the ground-truth
deterioration progress (p >= 0.3, i.e. clinically developing).
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from .physio import STEPS_PER_DAY, make_patient
from .policies import RiskModel, VeriLoop
from .sim import Env

LABEL_P = 0.3


class Recorder(VeriLoop):
    def __init__(self, scale2):
        dummy = RiskModel(np.zeros(16), -10.0, np.zeros(16), np.ones(16))
        super().__init__(scale2, dummy, adaptive_sampling=False)
        self.X, self.y = [], []
        self.env = None

    def observe(self, t, t_obs, obs):
        clean, _ = self._clean(obs) if obs is not None else (None, None)
        if obs is not None:
            self.hist.append(obs)
        self.ft.update(t, clean)
        f = self.ft.features()
        if f is not None and t >= STEPS_PER_DAY:
            self.X.append(f)
            self.y.append(int(self.env.ep_active and self.env.p >= LABEL_P))
        return []

    def feedback(self, t, fb):
        return []


def build_dataset(seed, n_patients, days=7):
    X, y = [], []
    for pid in range(n_patients):
        rng = np.random.default_rng([seed, pid])
        pt = make_patient(pid, rng, days)
        # Enrich the training cohort with deterioration episodes.
        while not pt.episodes:
            pt = make_patient(pid, rng, days)
        env = Env(pt, np.random.default_rng([seed, pid, 1]))
        rec = Recorder(pt.copd_scale2)
        rec.env = env
        env.run(rec)
        X += rec.X
        y += rec.y
    return np.array(X), np.array(y)


def train(seed=1000, n_patients=120):
    X, y = build_dataset(seed, n_patients)
    mean, scale = X.mean(0), X.std(0) + 1e-6
    clf = LogisticRegression(C=0.5, max_iter=2000, class_weight="balanced")
    clf.fit((X - mean) / scale, y)
    model = RiskModel(clf.coef_[0], float(clf.intercept_[0]), mean, scale)
    Xv, yv = build_dataset(seed + 1, n_patients // 2)
    pv = np.array([model(f) for f in Xv])
    stats = dict(n_train=int(len(y)), pos_rate=float(y.mean()), val_auroc=float(roc_auc_score(yv, pv)),
                 val_auprc=float(average_precision_score(yv, pv)), val_pos_rate=float(yv.mean()))
    return model, stats
