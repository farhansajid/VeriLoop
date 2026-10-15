# VeriLoop — Internal Paper Submission Checklist & Notes

> **Notice:** This document contains internal pre-submission notes, magazine constraints, and action items for the authors (Dr. Muhammad Farhan, Dr. Khalid Mahmood, and Collaborators) prior to final submission to the **IEEE Internet of Things Magazine** Special Issue: *"Digital Service Loops for IoT-Enabled Connected Healthcare Systems"*.

---

## 📌 Submission Deadline
- **Target Deadline:** 31 October 2026
- **Target Venue:** IEEE Internet of Things Magazine (MIOT)
- **Special Issue:** *Digital Service Loops for IoT-Enabled Connected Healthcare Systems*

---

## 📋 What is Here

| Folder / File | Contents |
|---|---|
| `paper/` | `main.tex` (IEEEtran), `refs.bib` (15 references), `figures/` (TikZ diagrams: `overview.tex` Fig. 1, `lifecycle.tex` Fig. 2, `methodology.tex` Fig. 3; plots `case_trace.pdf`, `results.pdf`; generated table rows), compiled `main.pdf`. `architecture.tex` is the earlier Fig. 1 and is no longer used. |
| `paper_miot/` | **Submission version** in the official IEEE IoT Magazine template (`IEEEmiot.cls`, `mcomstd.sty` from `IEEEMIOT.zip`): `main.tex`, compiled `main.pdf`, `figures/`, `refs.bib`. |
| `code/` | Simulator and VeriLoop implementation (`veriloop/`), experiment runner, figure generation, statistics, case-study and ledger-benchmark scripts. |
| `results/` | All empirical numbers used in the paper: `summary.json` (mean ± 95% CI), `per_seed_metrics.json`, `stats.json` (paired differences, Fisher tests), `ledger_benchmark.json`, `risk_model.json`, raw per-patient runs (`runs.pkl`). |
| `IEEEMIOT.zip` | Original IEEE Internet of Things Magazine LaTeX template package. |

---

## 🔄 Reproduction Commands (≈20 min on 4 cores)

```bash
cd code
pip install -r requirements.txt
python run_experiments.py          # Trains the risk model, runs 9 designs × 5 seeds × 150 patients
python ledger_benchmark.py         # Audit-log overhead and tamper detection benchmark
python case_study.py               # Generates Fig. 4 (case trace)
python make_figures.py             # Generates Fig. 5 and Table I rows (table_combined.tex; paste into main.tex)
python stats.py                    # Computes paired statistics and Fisher exact tests quoted in text
cd ../paper_miot && pdflatex main && bibtex main && pdflatex main && pdflatex main   # Build submission paper
# (Note: copy regenerated figures/case_trace.pdf and figures/results.pdf from paper/figures first if re-plotted)
```

> **Note:** `make_figures.py` writes the table rows to `paper/figures/table_*.tex`; these rows are pasted inline in `main.tex` because `\input` inside a `booktabs` table can break `\bottomrule`.

---

## 🩺 Physiological & Simulation Model in Brief

Each virtual patient (4 sub-groups including COPD on NEWS2 $\text{SpO}_2$ scale 2, heart failure, and tachycardic/hypertensive phenotypes) is simulated at 5-minute resolution for 7 days with circadian rhythm, autocorrelated noise, benign exertion, and septic/respiratory deterioration episodes that become critical in 3–10 hours unless treated. Sensors suffer spikes, dropouts, frozen channels, drifting probes, and detachment; home broadband has periodic outages. Patients answer re-measure/device-check requests and clinicians answer teleconsults and escalations with stochastic delays and treatment success probabilities. All parameters are configured in `code/veriloop/physio.py` and `code/veriloop/sim.py`.

---

## ⚠️ Pre-Submission Action Items for Authors

1. **Authors & Affiliations:**
   - Update placeholder author block in `paper_miot/main.tex`:
     ```latex
     \author{{First A. Author, Second B. Author, and Third C. Author}}
     ```
   - Provide IEEE membership grades, ORCIDs, institutional departments, and emails.
   - Fill in or remove `\funding{...}`.
   - Replace placeholder biographies at the end of `paper_miot/main.tex`.

2. **Verify Magazine Word & Item Limits:**
   - Review IEEE IoT Magazine *Information for Authors*.
   - Expected limits: ~4,500 words from introduction to conclusion, $\le 6$ figures + tables, $\le 15$ references, minimal equations.
   - Current draft: ~3,100 body words, 5 figures + 1 table, 15 references, 0 equations. (Within all standard limits).

3. **Verify References:**
   - Double-check all entries in `refs.bib` against official publisher entries (titles, pages, publication years, DOIs).

4. **Simulation Context:**
   - The paper notes explicitly that current evaluation relies on a high-fidelity discrete-event simulator. If real RPM cohort calibration data becomes available prior to submission or revision, consider adding retrospective validation.

5. **Code Availability Statement:**
   - Update the repository link in `paper_miot/main.tex` (`\section{Code and Data Availability}`) to point to the newly published GitHub repository:
     `https://github.com/farhansajid/VeriLoop`

6. **HL7 FHIR Implementation Note:**
   - The FHIR mapping in `code/veriloop/fhir.py` is a structural illustration (`Observation`, `ServiceRequest`, `Task`, `Provenance`) demonstrating edge-to-EHR interoperability, rather than a full certified server.
