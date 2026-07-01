-- =============================================================================
-- analysis_queries.sql
-- STEP 3 OF THE PROJECT: The core RCM (Revenue Cycle Management) SQL analysis.
-- =============================================================================
--
-- HOW TO RUN THESE: open data/hospital.db with any SQLite tool, or run them
-- from Python using sqlite3 (see notebooks/analysis.py, which runs these same
-- queries and prints/plots the results).
--
-- Each query below has a comment explaining:
--   (a) the business question it answers
--   (b) the SQL concept/technique it demonstrates
--   (c) why that technique was the right choice
-- =============================================================================


-- -----------------------------------------------------------------------------
-- QUERY 1: Claim status funnel — how many claims end up in each final status?
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: Of all claims we submitted, what % got paid first-time,
-- what % were denied-then-paid-on-appeal, and what % were ultimately written
-- off (lost revenue)?
--
-- CONCEPT: each claim has MULTIPLE rows in claim_events (its full history).
-- To get each claim's FINAL status, we need the event with the LATEST
-- event_date per claim_id. This is a classic "latest record per group"
-- problem — solved here with a window function (ROW_NUMBER), which is one
-- of the most commonly asked SQL interview patterns.
-- -----------------------------------------------------------------------------
WITH ranked_events AS (
    SELECT
        claim_id,
        event_status,
        event_date,
        denial_reason,
        ROW_NUMBER() OVER (PARTITION BY claim_id ORDER BY event_date DESC) AS rn
        -- PARTITION BY claim_id  -> restart the row numbering for each claim
        -- ORDER BY event_date DESC -> rank 1 = the most recent event = final status
    FROM claim_events
),
final_status AS (
    SELECT claim_id, event_status AS final_status, denial_reason
    FROM ranked_events
    WHERE rn = 1   -- keep only the latest event per claim
)
SELECT
    final_status,
    COUNT(*) AS num_claims,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM claims), 1) AS pct_of_all_claims
FROM final_status
GROUP BY final_status
ORDER BY num_claims DESC;


-- -----------------------------------------------------------------------------
-- QUERY 2: Denial rate by payer — which insurance companies deny us most?
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: Is our denial problem spread evenly, or concentrated
-- with specific payers? This is exactly the kind of "segment before you
-- conclude" thinking discussed in the technical prep doc.
--
-- CONCEPT: a claim "was denied at some point" if it has ANY event with
-- status = 'Denied', even if it was later paid on appeal. We use a
-- correlated EXISTS subquery here, which is often cleaner than a JOIN when
-- you just need a yes/no flag rather than pulling columns from the other table.
-- -----------------------------------------------------------------------------
SELECT
    c.payer,
    COUNT(DISTINCT c.claim_id) AS total_claims,
    SUM(CASE WHEN EXISTS (
        SELECT 1 FROM claim_events ce
        WHERE ce.claim_id = c.claim_id AND ce.event_status = 'Denied'
    ) THEN 1 ELSE 0 END) AS claims_ever_denied,
    ROUND(100.0 * SUM(CASE WHEN EXISTS (
        SELECT 1 FROM claim_events ce
        WHERE ce.claim_id = c.claim_id AND ce.event_status = 'Denied'
    ) THEN 1 ELSE 0 END) / COUNT(DISTINCT c.claim_id), 1) AS denial_rate_pct
FROM claims c
WHERE c.payer != 'Self-Pay'   -- self-pay claims have no denial concept
GROUP BY c.payer
ORDER BY denial_rate_pct DESC;


-- -----------------------------------------------------------------------------
-- QUERY 3: Top denial reasons — what's actually causing the denials?
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: If we wanted to fix our denial rate, what's the single
-- biggest lever? (e.g. if "Prior authorization missing" is #1, that points
-- to a front-end process fix, not a coding fix.)
-- -----------------------------------------------------------------------------
SELECT
    denial_reason,
    COUNT(*) AS num_denials,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM claim_events WHERE event_status = 'Denied'), 1) AS pct_of_denials
FROM claim_events
WHERE event_status = 'Denied'
GROUP BY denial_reason
ORDER BY num_denials DESC;


-- -----------------------------------------------------------------------------
-- QUERY 4: Days in Accounts Receivable (AR) — how long does it take to get paid?
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: "Days in AR" is one of the most-watched metrics in RCM —
-- it's the number of days between when a claim was submitted and when it was
-- finally paid. A long AR cycle means cash is tied up and not yet collected.
--
-- CONCEPT: we need the EARLIEST 'Submitted' date and the (one) 'Paid' date
-- per claim, then subtract. We do this with two separate aggregated
-- subqueries joined together — a common pattern when you need two different
-- "first/only event of type X" values from the same events table.
-- -----------------------------------------------------------------------------
WITH submitted AS (
    SELECT claim_id, MIN(event_date) AS submitted_date
    FROM claim_events
    WHERE event_status = 'Submitted'
    GROUP BY claim_id
),
paid AS (
    SELECT claim_id, MAX(event_date) AS paid_date  -- MAX in case of edge cases; normally only one 'Paid' event
    FROM claim_events
    WHERE event_status = 'Paid'
    GROUP BY claim_id
)
SELECT
    c.payer,
    COUNT(*) AS paid_claims,
    ROUND(AVG(JULIANDAY(p.paid_date) - JULIANDAY(s.submitted_date)), 1) AS avg_days_in_ar,
    -- JULIANDAY() converts a date into a number, so subtracting two of them gives a day count (SQLite-specific date function)
    MIN(JULIANDAY(p.paid_date) - JULIANDAY(s.submitted_date)) AS min_days,
    MAX(JULIANDAY(p.paid_date) - JULIANDAY(s.submitted_date)) AS max_days
FROM paid p
JOIN submitted s ON p.claim_id = s.claim_id
JOIN claims c ON c.claim_id = p.claim_id
GROUP BY c.payer
ORDER BY avg_days_in_ar DESC;


-- -----------------------------------------------------------------------------
-- QUERY 5: 30-day readmissions — which patients came back too soon?
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: a 30-day readmission (a patient discharged and then
-- re-admitted within 30 days) is a widely tracked clinical quality and cost
-- metric — frequent readmissions can signal a quality-of-care issue and also
-- cost the hospital money if payers penalize for it.
--
-- CONCEPT: this is the LAG() window function pattern — for each encounter,
-- look "back" at that same patient's PREVIOUS discharge_date, ordered by
-- admit_date. This is one of the most useful window-function patterns in
-- healthcare analytics and a very likely live-coding question.
-- -----------------------------------------------------------------------------
WITH encounter_with_prev AS (
    SELECT
        encounter_id,
        patient_id,
        department,
        admit_date,
        discharge_date,
        LAG(discharge_date) OVER (PARTITION BY patient_id ORDER BY admit_date) AS prev_discharge_date
        -- LAG() looks at the PREVIOUS row (by admit_date) for the SAME patient
        -- and pulls that row's discharge_date into the current row
    FROM encounters
)
SELECT
    encounter_id,
    patient_id,
    department,
    prev_discharge_date,
    admit_date,
    CAST(JULIANDAY(admit_date) - JULIANDAY(prev_discharge_date) AS INTEGER) AS days_since_last_discharge
FROM encounter_with_prev
WHERE prev_discharge_date IS NOT NULL
  AND JULIANDAY(admit_date) - JULIANDAY(prev_discharge_date) <= 30
ORDER BY days_since_last_discharge ASC;


-- -----------------------------------------------------------------------------
-- QUERY 6: Readmission rate by department — where is it concentrated?
-- -----------------------------------------------------------------------------
-- Builds on Query 5: turns the raw list of readmissions into a rate per
-- department, which is the version you'd actually present to stakeholders.
-- -----------------------------------------------------------------------------
WITH encounter_with_prev AS (
    SELECT
        encounter_id, patient_id, department, admit_date, discharge_date,
        LAG(discharge_date) OVER (PARTITION BY patient_id ORDER BY admit_date) AS prev_discharge_date
    FROM encounters
),
flagged AS (
    SELECT
        *,
        CASE
            WHEN prev_discharge_date IS NOT NULL
                 AND JULIANDAY(admit_date) - JULIANDAY(prev_discharge_date) <= 30
            THEN 1 ELSE 0
        END AS is_readmission
    FROM encounter_with_prev
)
SELECT
    department,
    COUNT(*) AS total_encounters,
    SUM(is_readmission) AS readmissions,
    ROUND(100.0 * SUM(is_readmission) / COUNT(*), 1) AS readmission_rate_pct
FROM flagged
GROUP BY department
ORDER BY readmission_rate_pct DESC;


-- -----------------------------------------------------------------------------
-- QUERY 7: DRG-level financial performance — billed amount vs. DRG base rate
-- -----------------------------------------------------------------------------
-- BUSINESS QUESTION: for each DRG, are we billing roughly in line with the
-- expected reimbursement, or are some DRGs consistently over/under? This is
-- the DRG financial-performance analysis described in the technical prep doc.
-- -----------------------------------------------------------------------------
SELECT
    e.drg_code,
    COUNT(*) AS num_cases,
    ROUND(AVG(c.billed_amount), 0) AS avg_billed_amount,
    ROUND(AVG(e.length_of_stay_days), 1) AS avg_length_of_stay
FROM encounters e
JOIN claims c ON c.encounter_id = e.encounter_id
GROUP BY e.drg_code
ORDER BY avg_billed_amount DESC;
