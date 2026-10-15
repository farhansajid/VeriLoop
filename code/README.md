# VeriLoop Simulation & Core Architecture Codebase

This directory houses the complete Python implementation of **VeriLoop**, including the discrete-event physiological simulation environment, tiered policies, ML risk estimation, multi-channel sensor fault diagnosis, HL7 FHIR R4 mapping, and the SHA-256 tamper-evident provenance ledger.

---

## 📁 Code Organization

```
code/
├── veriloop/                    # Core Python package
│   ├── __init__.py              # Package init
│   ├── physio.py                # Virtual patient models, NEWS2 physiological scoring, noise & deterioration
│   ├── sim.py                   # Discrete-event simulation engine (Env), sensor faults & broadband outages
│   ├── policies.py              # VeriLoop agent, Spot NEWS2, Cont NEWS2, Open-loop ML, & SHA-256 Ledger
│   ├── train.py                 # Logistic regression risk model trainer (16 personal/trend features)
│   ├── experiments.py           # Multi-worker experiment harness & design configurations
│   ├── metrics.py               # Statistical aggregation, Wilson score intervals & mean 95% CIs
│   └── fhir.py                  # HL7 FHIR R4 interoperability mapping (Observation, ServiceRequest, Task, Provenance)
│
├── run_experiments.py           # Master evaluation runner across 9 designs × 5 seeds × 150 patients
├── ledger_benchmark.py          # Ledger append/verify timing & 1,000-sample attack tamper-evidence test
├── case_study.py                # Replays patient trace demonstrating outcome verification (Fig. 4)
├── make_figures.py              # Generates publication PDF/PNG figures and LaTeX table rows
├── stats.py                     # Computes Fisher exact tests and paired seed statistics
└── requirements.txt             # Python dependencies
```

---

## 🔬 Core Modules in `veriloop/`

1. **`veriloop.physio`**:
   - Implements 4 virtual patient cohorts: General, COPD (NEWS2 $\text{SpO}_2$ scale 2), Heart Failure, and Tachycardic/Hypertensive phenotypes.
   - Generates 5 vital signs: Heart Rate (HR), Respiratory Rate (RR), Oxygen Saturation ($\text{SpO}_2$), Systolic Blood Pressure (SBP), and Body Temperature (TEMP).
   - Models 24-hour circadian rhythms, AR(1) autocorrelated physiological noise, diurnal exertion, and septic/respiratory acute deteriorations.

2. **`veriloop.sim`**:
   - Discrete-event simulation environment (`Env`) running at 5-minute time steps for 7 simulated days (2,016 steps per patient).
   - Injects realistic sensor faults: value spikes, dropouts, frozen channels, baseline drifts, and complete sensor probe detachment.
   - Injects periodic home broadband outages (~every 2 days) with seamless cellular alert fallback.
   - Simulates human response delays and treatment outcomes for both patients and clinicians.

3. **`veriloop.policies`**:
   - Implements baseline policies (`SpotNEWS2`, `ContNEWS2`, `OpenLoopML`) and the proposed `VeriLoop` service loop.
   - Multi-stage logic: Range checks $\to$ Spikes/Frozen/Drift fault detection $\to$ Personal baseline calibration $\to$ 16-feature risk evaluation $\to$ Tiered service contracts $\to$ Outcome verification $\to$ Bounded adaptation.
   - Embedded `Ledger`: Local SHA-256 hash-chaining of every loop iteration with hourly cloud anchor synchronization.

4. **`veriloop.train`**:
   - Trains an inspectable L1/L2-regularized logistic regression model on 16 features (deviations from 24h baseline, 1h rolling mean, 30min trend, and aggregate NEWS2).

5. **`veriloop.fhir`**:
   - Maps vital observations to LOINC-coded FHIR `Observation`.
   - Generates `ServiceRequest` and tracking `Task` resources with status updates.
   - Embeds ledger cryptographic hash signatures within FHIR `Provenance` resources.

---

## 🚀 Running the Code

All scripts should be executed from within the `code/` directory:

```bash
cd code
pip install -r requirements.txt

# 1. Run main experiments (trains risk model and simulates 750 patient-weeks)
python run_experiments.py --patients 150 --seeds 5 --workers 4

# 2. Benchmark ledger throughput and verify 100% tamper detection
python ledger_benchmark.py

# 3. Generate case study trace plot
python case_study.py

# 4. Generate publication figures and LaTeX tables
python make_figures.py

# 5. Output statistical significance tests
python stats.py
```
