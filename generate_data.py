"""
generate_data.py
=================
STEP 1 OF THE PROJECT: Generate a realistic, synthetic hospital claims dataset.

WHY THIS STEP EXISTS (concept, explained simply):
--------------------------------------------------
Real hospital data is protected by strict privacy laws (HIPAA in the US, similar
rules elsewhere) and you'd never get access to real patient records for a
portfolio project. So the standard practice in healthcare analytics — and a
perfectly legitimate thing to say in an interview — is to build SYNTHETIC data
that mimics the structure, relationships, and statistical patterns of real
hospital data, without it being real.

We are generating four related tables, the same way a real hospital's systems
would store this data:
  1. patients      -> one row per patient (who they are)
  2. encounters     -> one row per hospital visit/admission (when they came in)
  3. claims         -> one row per insurance claim tied to an encounter (the bill)
  4. claim_events    -> one row per status change on a claim (its lifecycle)

This mirrors a real Revenue Cycle Management (RCM) system: a patient has
encounters, each encounter generates a claim, and each claim moves through a
lifecycle of submitted -> paid / denied -> (optionally) appealed -> paid/written off.

We deliberately bake in REALISTIC PROBLEMS into the data (some claims get denied,
some patients get readmitted within 30 days, some claims take a long time to
get paid) so that the analysis later actually finds something meaningful —
just like in a real hospital dataset.
"""

import pandas as pd
import numpy as np
from faker import Faker
import random
from datetime import timedelta

# Setting a "seed" makes the random data reproducible — running this script
# twice will generate the exact same dataset, which is useful for debugging
# and for showing consistent results in an interview.
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

N_PATIENTS = 1500
N_ENCOUNTERS = 4000

DEPARTMENTS = ["Cardiology", "Orthopedics", "General Medicine", "Oncology",
               "Emergency", "Pediatrics", "Obstetrics", "Neurology", "ICU", "Outpatient Surgery"]

PAYERS = ["Daman", "AXA Gulf", "Thiqa (Government)", "NextCare", "Saudi German Health Insurance", "Self-Pay"]

# A small, realistic slice of CPT (procedure) codes and ICD-10 (diagnosis) codes.
# In a real project you wouldn't invent these — you'd reference the official
# CPT/ICD code sets — but for a portfolio project, a believable sample is enough.
CPT_CODES = {
    "99213": "Office visit, established patient",
    "99284": "Emergency dept visit, high severity",
    "27447": "Total knee replacement",
    "33533": "Coronary artery bypass graft",
    "59400": "Vaginal delivery, routine care",
    "19303": "Mastectomy",
    "70450": "CT scan, head, without contrast",
    "93000": "Electrocardiogram",
}
ICD_CODES = {
    "I21.9": "Acute myocardial infarction",
    "M17.0": "Osteoarthritis of knee",
    "J18.9": "Pneumonia",
    "C50.9": "Malignant neoplasm of breast",
    "O80": "Normal delivery",
    "I50.9": "Heart failure",
    "E11.9": "Type 2 diabetes mellitus",
    "S72.0": "Fracture of femur",
}

DRG_TABLE = {
    # DRG code -> (description, base reimbursement amount in AED)
    "470": ("Major joint replacement, no complications", 28000),
    "291": ("Heart failure with major complications", 19500),
    "194": ("Pneumonia, with complications", 11000),
    "765": ("Vaginal delivery, no complications", 9000),
    "247": ("Coronary stent, no complications", 32000),
    "057": ("Degenerative nervous system disorder", 8500),
}

DENIAL_REASONS = [
    "Prior authorization missing",
    "Medical necessity not established",
    "Incorrect patient information",
    "Duplicate claim",
    "Service not covered under plan",
    "Coding error",
]

print("STEP 1: Generating patients table...")

# ---------------------------------------------------------------------------
# TABLE 1: patients
# Concept: this is a "dimension" table — it describes WHO, and doesn't change
# often. In data-modeling terms (see the technical prep doc), this is a
# classic dimension table that other tables will reference by patient_id.
# ---------------------------------------------------------------------------
patients = []
for i in range(1, N_PATIENTS + 1):
    patients.append({
        "patient_id": f"P{i:05d}",
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "date_of_birth": fake.date_of_birth(minimum_age=1, maximum_age=95),
        "gender": random.choice(["M", "F"]),
        "payer": random.choices(PAYERS, weights=[30, 20, 25, 10, 5, 10])[0],
    })
patients_df = pd.DataFrame(patients)

print("STEP 2: Generating encounters table...")

# ---------------------------------------------------------------------------
# TABLE 2: encounters
# Concept: this is a "fact" table — it represents an EVENT (a hospital visit)
# that happened at a point in time, and it references the patients dimension
# via patient_id (a "foreign key" — the link between the two tables).
#
# We deliberately make some patients have MULTIPLE encounters close together,
# which is what creates "readmissions" for us to detect later.
# ---------------------------------------------------------------------------
encounters = []
encounter_counter = 1
start_date = pd.Timestamp("2025-01-01")

for _, pat in patients_df.iterrows():
    # Most patients have 1 encounter; some have 2-4 (these are our readmission candidates)
    n_enc = np.random.choice([1, 2, 3, 4], p=[0.65, 0.22, 0.09, 0.04])
    last_discharge = None
    for n in range(n_enc):
        if last_discharge is None:
            admit_date = start_date + timedelta(days=int(np.random.uniform(0, 300)))
        else:
            # 35% chance the next encounter is a quick readmission (within 30 days)
            if random.random() < 0.35:
                gap = random.randint(1, 29)
            else:
                gap = random.randint(31, 200)
            admit_date = last_discharge + timedelta(days=gap)

        dept = random.choice(DEPARTMENTS)
        los_days = max(1, int(np.random.exponential(scale=4)))  # length of stay, right-skewed like real LOS
        discharge_date = admit_date + timedelta(days=los_days)
        last_discharge = discharge_date

        cpt = random.choice(list(CPT_CODES.keys()))
        icd = random.choice(list(ICD_CODES.keys()))
        drg = random.choice(list(DRG_TABLE.keys()))

        encounters.append({
            "encounter_id": f"E{encounter_counter:06d}",
            "patient_id": pat["patient_id"],
            "department": dept,
            "admit_date": admit_date,
            "discharge_date": discharge_date,
            "length_of_stay_days": los_days,
            "cpt_code": cpt,
            "icd_code": icd,
            "drg_code": drg,
        })
        encounter_counter += 1

encounters_df = pd.DataFrame(encounters)
print(f"  -> Generated {len(encounters_df)} encounters for {len(patients_df)} patients")

print("STEP 3: Generating claims table...")

# ---------------------------------------------------------------------------
# TABLE 3: claims
# Concept: one claim per encounter, billed to the patient's insurance payer.
# This is where REVENUE CYCLE MANAGEMENT (RCM) starts: a claim is the formal
# request for payment sent to an insurance company after a service is provided.
#
# We assign a "billed_amount" based on the DRG's base reimbursement, with some
# random variation (real billed amounts vary around a DRG's typical rate).
# ---------------------------------------------------------------------------
claims = []
claim_counter = 1
for _, enc in encounters_df.iterrows():
    base_amount = DRG_TABLE[enc["drg_code"]][1]
    billed_amount = round(base_amount * np.random.uniform(0.85, 1.25), 2)
    patient_payer = patients_df.loc[patients_df.patient_id == enc.patient_id, "payer"].values[0]

    claims.append({
        "claim_id": f"C{claim_counter:06d}",
        "encounter_id": enc["encounter_id"],
        "patient_id": enc["patient_id"],
        "payer": patient_payer,
        "billed_amount": billed_amount,
        "submission_date": enc["discharge_date"] + timedelta(days=random.randint(1, 5)),
    })
    claim_counter += 1

claims_df = pd.DataFrame(claims)

print("STEP 4: Generating claim_events table (the claim lifecycle)...")

# ---------------------------------------------------------------------------
# TABLE 4: claim_events
# Concept: a claim doesn't just have one final status — it moves through a
# LIFECYCLE: Submitted -> Paid (first pass) OR Denied -> (if denied) Appealed
# -> Paid or Written Off. Modeling this as a separate "events" table (rather
# than just one status column) is what lets us measure things like
# "days in AR" (Accounts Receivable) and denial-to-resolution time later.
#
# This is a realistic design choice you can explain in an interview: a single
# status column loses history; an events table preserves the full timeline.
# ---------------------------------------------------------------------------
claim_events = []
event_counter = 1

# Self-pay claims behave differently (no payer denial process), so we treat them separately
for _, claim in claims_df.iterrows():
    submission_date = claim["submission_date"]
    claim_events.append({
        "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
        "event_status": "Submitted", "event_date": submission_date, "denial_reason": None
    })
    event_counter += 1

    if claim["payer"] == "Self-Pay":
        # Self-pay just gets paid (collected) after some delay, no denial concept
        paid_date = submission_date + timedelta(days=random.randint(5, 60))
        claim_events.append({
            "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
            "event_status": "Paid", "event_date": paid_date, "denial_reason": None
        })
        event_counter += 1
        continue

    # ~22% of insured claims get denied on first submission — a realistic-ish denial rate
    is_denied = random.random() < 0.22
    if not is_denied:
        paid_date = submission_date + timedelta(days=int(np.random.gamma(shape=3, scale=7)))  # right-skewed AR days
        claim_events.append({
            "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
            "event_status": "Paid", "event_date": paid_date, "denial_reason": None
        })
        event_counter += 1
    else:
        denied_date = submission_date + timedelta(days=random.randint(7, 21))
        reason = random.choice(DENIAL_REASONS)
        claim_events.append({
            "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
            "event_status": "Denied", "event_date": denied_date, "denial_reason": reason
        })
        event_counter += 1

        # Of denied claims, 60% get appealed
        if random.random() < 0.60:
            appeal_date = denied_date + timedelta(days=random.randint(3, 15))
            claim_events.append({
                "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
                "event_status": "Appealed", "event_date": appeal_date, "denial_reason": reason
            })
            event_counter += 1

            # Of appealed claims, 65% eventually get paid, 35% get written off
            if random.random() < 0.65:
                final_date = appeal_date + timedelta(days=random.randint(10, 45))
                claim_events.append({
                    "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
                    "event_status": "Paid", "event_date": final_date, "denial_reason": None
                })
            else:
                final_date = appeal_date + timedelta(days=random.randint(10, 30))
                claim_events.append({
                    "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
                    "event_status": "Written Off", "event_date": final_date, "denial_reason": reason
                })
            event_counter += 1
        else:
            # Not appealed -> straight to written off
            final_date = denied_date + timedelta(days=random.randint(20, 60))
            claim_events.append({
                "event_id": f"EV{event_counter:06d}", "claim_id": claim["claim_id"],
                "event_status": "Written Off", "event_date": final_date, "denial_reason": reason
            })
            event_counter += 1

claim_events_df = pd.DataFrame(claim_events)

print("STEP 5: Saving tables to /data as CSV (and one combined SQLite database)...")

# Save as CSV — simple, human-readable, easy to open in Excel to sanity-check
patients_df.to_csv("C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/patients.csv", index=False)
encounters_df.to_csv("C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/encounters.csv", index=False)
claims_df.to_csv("C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/claims.csv", index=False)
claim_events_df.to_csv("C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/claim_events.csv", index=False)

# Also save as a SQLite database, so you can practice and demo real SQL queries
# (SQLite is a lightweight, file-based database — perfect for a portfolio
# project since it needs no server setup, but the SQL you write is standard
# SQL that transfers directly to SQL Server, Fabric, Redshift, etc.)
import sqlite3
conn = sqlite3.connect("C:/Users/JR/OneDrive/Learning/hospital_rcm_project/hospital_rcm_project/data/hospital.db")
patients_df.to_sql("patients", conn, if_exists="replace", index=False)
encounters_df.to_sql("encounters", conn, if_exists="replace", index=False)
claims_df.to_sql("claims", conn, if_exists="replace", index=False)
claim_events_df.to_sql("claim_events", conn, if_exists="replace", index=False)
conn.close()

print("\nDONE. Summary of generated data:")
print(f"  patients:      {len(patients_df):,} rows")
print(f"  encounters:    {len(encounters_df):,} rows")
print(f"  claims:        {len(claims_df):,} rows")
print(f"  claim_events:  {len(claim_events_df):,} rows")
print("\nFiles written to /data/*.csv and /data/hospital.db")
