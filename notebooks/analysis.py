"""
analysis.py
===========
STEP 4 OF THE PROJECT: Python-side analysis — statistics, modeling, and charts.

This script picks up where the SQL queries left off (sql/analysis_queries.sql)
and does the parts that are easier in Python than SQL: statistical testing,
a simple risk model, and visualizations to present findings.

WHY SPLIT WORK BETWEEN SQL AND PYTHON? (concept for the interview)
--------------------------------------------------------------------
A common, very realistic workflow in analytics roles: use SQL to pull and
shape data close to the database (filtering, joining, aggregating — things
databases are optimized for), then use Python for anything needing
statistics, modeling, or visualization that SQL doesn't handle well. This
script demonstrates exactly that handoff.
"""

import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
import matplotlib
matplotlib.use("Agg")  # write charts to files instead of trying to open a window
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="whitegrid")
DB_PATH = "C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/hospital.db"
OUT_DIR = "C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/output"

conn = sqlite3.connect(DB_PATH)

print("=" * 70)
print("PART 1: Load the SQL analysis results back into Python")
print("=" * 70)

# -----------------------------------------------------------------------------
# We re-run a couple of the SQL queries here directly from Python using
# pandas.read_sql_query() — this is the standard way analysts move data from
# a database into a Python environment for further work.
# -----------------------------------------------------------------------------

denial_by_payer = pd.read_sql_query("""
    SELECT
        c.payer,
        COUNT(DISTINCT c.claim_id) AS total_claims,
        SUM(CASE WHEN EXISTS (
            SELECT 1 FROM claim_events ce
            WHERE ce.claim_id = c.claim_id AND ce.event_status = 'Denied'
        ) THEN 1 ELSE 0 END) AS claims_ever_denied
    FROM claims c
    WHERE c.payer != 'Self-Pay'
    GROUP BY c.payer
""", conn)
denial_by_payer["denial_rate"] = denial_by_payer["claims_ever_denied"] / denial_by_payer["total_claims"]
print("\nDenial rate by payer:")
print(denial_by_payer)


print("\n" + "=" * 70)
print("PART 2: Statistical test — is the denial-rate difference between")
print("payers statistically significant, or could it just be random noise?")
print("=" * 70)

# -----------------------------------------------------------------------------
# CONCEPT: a chi-square test of independence.
# We have a categorical outcome (denied: yes/no) across multiple categorical
# groups (payer). The chi-square test answers: "if denial rate were truly
# the same across all payers, how likely is it we'd see differences this
# large just by chance?" A small p-value (conventionally < 0.05) means the
# difference is unlikely to be random — i.e. statistically significant.
#
# This is exactly the kind of test discussed in the technical prep doc for
# "how would you compare readmission/denial rates between groups."
# -----------------------------------------------------------------------------
contingency_table = denial_by_payer.set_index("payer")[["claims_ever_denied"]].copy()
contingency_table["claims_not_denied"] = denial_by_payer.set_index("payer")["total_claims"] - denial_by_payer.set_index("payer")["claims_ever_denied"]

chi2, p_value, dof, expected = stats.chi2_contingency(contingency_table.values)

print(f"\nChi-square statistic: {chi2:.2f}")
print(f"Degrees of freedom:   {dof}")
print(f"P-value:              {p_value:.4f}")
if p_value < 0.05:
    print("-> P-value < 0.05: the denial rate differences across payers ARE")
    print("   statistically significant — this is a real pattern worth investigating,")
    print("   not just random noise.")
else:
    print("-> P-value >= 0.05: we cannot conclude the differences are statistically")
    print("   significant — could plausibly be random variation with this sample size.")


print("\n" + "=" * 70)
print("PART 3: Simple readmission-risk flagging (rule-based, explainable)")
print("=" * 70)

# -----------------------------------------------------------------------------
# CONCEPT: for a portfolio project at this stage, a simple, explainable
# RULE-BASED risk flag is often more appropriate (and more honest about
# project scope) than building a full machine-learning classifier on
# synthetic data with limited real signal. This mirrors a real first step
# in a hospital analytics engagement: establish a transparent baseline
# before justifying the complexity of a full predictive model.
#
# We flag a patient as "high readmission risk" if:
#   - they have 2+ encounters in the dataset, AND
#   - at least one of their prior encounters had length_of_stay > 7 days
#     (a longer stay often signals a more complex, higher-risk case)
# -----------------------------------------------------------------------------

encounters = pd.read_sql_query("SELECT * FROM encounters", conn, parse_dates=["admit_date", "discharge_date"])

patient_encounter_counts = encounters.groupby("patient_id").size().rename("num_encounters")
patient_max_los = encounters.groupby("patient_id")["length_of_stay_days"].max().rename("max_los")

risk_table = pd.concat([patient_encounter_counts, patient_max_los], axis=1).reset_index()
risk_table["high_risk_flag"] = (
    (risk_table["num_encounters"] >= 2) & (risk_table["max_los"] > 7)
).astype(int)

print(f"\nTotal patients: {len(risk_table)}")
print(f"Patients flagged high-risk: {risk_table['high_risk_flag'].sum()} "
      f"({100 * risk_table['high_risk_flag'].mean():.1f}%)")

risk_table.to_csv(f"{OUT_DIR}/patient_readmission_risk_flags.csv", index=False)
print(f"Saved patient-level risk flags to {OUT_DIR}/patient_readmission_risk_flags.csv")

print("\nNOTE for the interview: this rule-based approach is a deliberate first")
print("step. A natural next iteration --- and a good thing to mention if asked")
print("'what would you do next' --- would be a logistic regression model using")
print("age, department, prior LOS, and diagnosis as predictors, validated with")
print("the calibration and subgroup checks discussed in the technical prep doc.")


print("\n" + "=" * 70)
print("PART 4: DRG cost-variance analysis")
print("=" * 70)

# -----------------------------------------------------------------------------
# CONCEPT: for each DRG, compare actual billed_amount against that DRG's
# fixed base reimbursement rate. Cases billed well above the base rate are
# where the hospital is most exposed financially (since DRG reimbursement is
# generally fixed regardless of actual cost).
# -----------------------------------------------------------------------------
DRG_BASE_RATES = {
    "470": 28000, "291": 19500, "194": 11000,
    "765": 9000, "247": 32000, "057": 8500,
}

claims = pd.read_sql_query("SELECT * FROM claims", conn)
enc_claims = encounters.merge(claims, on=["encounter_id", "patient_id"])
enc_claims["drg_base_rate"] = enc_claims["drg_code"].map(DRG_BASE_RATES)
enc_claims["variance_pct"] = 100 * (enc_claims["billed_amount"] - enc_claims["drg_base_rate"]) / enc_claims["drg_base_rate"]

drg_variance = enc_claims.groupby("drg_code").agg(
    num_cases=("claim_id", "count"),
    avg_billed=("billed_amount", "mean"),
    base_rate=("drg_base_rate", "first"),
    avg_variance_pct=("variance_pct", "mean"),
).reset_index().sort_values("avg_variance_pct", ascending=False)

print("\nDRG cost variance vs. base reimbursement rate:")
print(drg_variance.to_string(index=False))


print("\n" + "=" * 70)
print("PART 5: Visualizations (saved to /output)")
print("=" * 70)

# Chart 1: Denial rate by payer (bar chart)
fig, ax = plt.subplots(figsize=(8, 5))
sns.barplot(data=denial_by_payer.sort_values("denial_rate", ascending=False),
            x="denial_rate", y="payer", ax=ax, color="#0F6B72")
ax.set_xlabel("Denial Rate")
ax.set_ylabel("Payer")
ax.set_title("Claim Denial Rate by Payer")
ax.xaxis.set_major_formatter(lambda x, _: f"{x*100:.0f}%")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/denial_rate_by_payer.png", dpi=150)
plt.close()
print(f"Saved {OUT_DIR}/denial_rate_by_payer.png")

# Chart 2: Length of stay distribution (histogram) — shows the right-skew discussed in the technical prep doc
fig, ax = plt.subplots(figsize=(8, 5))
sns.histplot(encounters["length_of_stay_days"], bins=30, color="#1F3864", ax=ax)
ax.set_xlabel("Length of Stay (days)")
ax.set_title("Length of Stay Distribution (right-skewed, as expected for LOS data)")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/length_of_stay_distribution.png", dpi=150)
plt.close()
print(f"Saved {OUT_DIR}/length_of_stay_distribution.png")

# Chart 3: DRG cost variance
fig, ax = plt.subplots(figsize=(8, 5))
bar_colors = ["#B7791F" if v > 0 else "#0F6B72" for v in drg_variance["avg_variance_pct"]]
ax.barh(drg_variance["drg_code"], drg_variance["avg_variance_pct"], color=bar_colors)
ax.invert_yaxis()
ax.set_xlabel("Avg. Billed vs. Base Rate Variance (%)")
ax.set_ylabel("DRG Code")
ax.set_title("DRG-Level Billing Variance vs. Base Reimbursement Rate")
ax.axvline(0, color="grey", linewidth=1)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/drg_cost_variance.png", dpi=150)
plt.close()
print(f"Saved {OUT_DIR}/drg_cost_variance.png")

conn.close()
print("\nAll done. See /output for CSVs and charts.")
