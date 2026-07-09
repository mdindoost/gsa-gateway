# Gate-2 precision fix — labeled regression fixture

> 47 `gate2:not-in-context` false-abstain candidates from the 2026-07-08 prose-gate diagnostic
> (`prose_gate_diag.jsonl`, gate ON / live OFF). Hand-labeled by reading each composed answer.
> **expected = the gate decision the FIXED gate2 must produce.** `keep` = a grounded span
> responds to the question's PRIMARY ask (gate wrongly abstained today). `abstain` = no span
> responds to the primary ask — drift / refusal / wrong-topic (gate is CORRECT today; MUST stay).

**Tally: 38 keep (false-abstain, 81%) · 9 abstain (gate correct, 19%).**

## MUST-STILL-ABSTAIN — guardrail cases (the fix may NOT flip these)

| # | question | why abstain is correct |
|---|----------|------------------------|
| 4 | can freshman work off campus? | tangential: asked off-campus work, answer is about ON-campus F-1 work |
| 5 | cn i take less credits at njit? | DRIFT: asked to take fewer credits, answer is about health-insurance full-time rule |
| 8 | last day fr networking event | refusal/no-recall: 'no specific mention of a networking event' |
| 16 | is there muslim student association at njit | not-owned/refusal: 'no mention of a Muslim Student Association' |
| 20 | wht is njit policy fr opt? | DRIFT: asked OPT policy, answer is about PATENT ownership |
| 31 | machine learning profesor citations? | DRIFT/wrong: asked ML prof citations, answer is a non-NJIT person, no citations |
| 43 | i20 sign wheree go | wrong sub-topic: asked where to sign i20, answer is about I-515A border arrivals |
| 45 | where submit scholarship form? | tangential: asked where to submit a scholarship form, answer is one specific (Guttenberg) admissions process |
| 79 | is there muslim stdent association at njit? | not-owned/refusal: 'no explicit mention of a Muslim Student Association' |

## MUST-KEEP — false-abstains the fix must recover

| # | question | rel | composed answer (start) |
|---|----------|-----|-------------------------|
| 0 | who teaching cs 634 next semester | 0.972 | According to document **doc_id 1867: Khalid Bakhshaliyev (Computer Science)**, **Khalid Ba |
| 1 | wher submit degreeworks form? | 0.634 | To submit the Degreeworks form, follow these steps:  1. Log into Highlander Pipeline. 2. S |
| 6 | i have problem with academic probation what do | 0.18 | According to the YWCC General FAQ document (doc_id 34454), being on academic warning/proba |
| 7 | how cpt apply | 0.98 | According to the Curricular Practical Training document (doc_id 32134), students must subm |
| 9 | how long does housing application take? | 0.803 | According to the document titled "Continuing Student Room Selection - Fall 2026 - Spring 2 |
| 11 | wheree can i print on campus | 0.882 | You can print on campus at the Hillier College of Architecture & Design (HCAD) Print Facil |
| 13 | i have problem with room change what do | 0.595 | According to doc_id 33035 (Residence Life), Room changes must be approved by the Associate |
| 14 | wht is njit policy for withdrawal? | 0.979 | According to the document titled "Withdrawals" in the Office of Financial Aid section (doc |
| 18 | wht is njit policy fr late payment? | 0.882 | According to the NJIT Payment Policy for Tuition & Fees document (doc_id 31451), "A $250 p |
| 19 | where cn i find info about graduation application? | 0.402 | According to doc_id 31523 (Office of the Registrar), you can access the Diploma Verificati |
| 21 | wher cn i print on campus | 0.555 | According to doc_id 35025 (Hillier College of Architecture & Design), the Hillier College  |
| 24 | where is canvas office? | 0.171 | According to doc_id 32071: Graduate Studies, specifically in the "Office of Digital Learni |
| 29 | i am f1 can i skip orientation? | 0.797 | According to the document titled "Undergraduate Level: Freshman (first year) students" whi |
| 32 | what is njit policy for housing application? | 0.982 | According to the document titled "Summer 2026 Housing" (doc_id 33061), NJIT policy for hou |
| 33 | how to drop a class | 0.889 | To drop a class at NJIT, according to the Fall 2021 Academic Calendar (doc_id 31547) provi |
| 36 | when is add drop? | 0.374 | According to the Winter 2022-2023 Academic Calendar document (doc_id 31541), the last day  |
| 38 | what is njit | 1.0 | New Jersey Institute of Technology (NJIT) is the state’s public polytechnic university in  |
| 40 | wht is njit | 0.991 | New Jersey Institute of Technology (NJIT) is the state’s public polytechnic university in  |
| 41 | i have problem w grade appeal what do | 0.745 | According to doc_id 32969 (Office of the Provost - Grade Appeal Policy), students have the |
| 44 | i have problem with course registration what do | 0.701 | According to the **Graduate Studies FAQ for Current Students** document (doc_id 32051), re |
| 46 | cn graduate student get transcript? | 0.411 | According to the transcript request section in doc_id 31517 (Office of the Registrar), cur |
| 47 | i20 sign where go | 0.405 | According to the Office of Global Initiatives APPLICATION FOR FORM I-20 (VSP20) document,  |
| 50 | can i withdraw late at njit? | 0.994 | According to the document titled "Withdrawals" in the Office of Financial Aid (doc_id 3168 |
| 52 | i have problem w class registration what do | 0.475 | According to doc_id 32051 (Graduate Studies FAQ for Current Students), when you have a reg |
| 54 | who teaching cs 634 next semester? | 0.959 | According to document **doc_id 1867 (Khalid Bakhshaliyev)**, Khalid Bakhshaliyev is teachi |
| 55 | wher can i print on campus | 0.747 | According to the Environmental & Operational Services document (Section: Print Facility),  |
| 57 | where cn i find info about degreeworks? | 0.24 | You can find information about DegreeWorks by logging into Highlander Pipeline, selecting  |
| 58 | do njit have graduation application? | 0.998 | According to the document titled "Graduation - Frequently Asked Questions" (doc_id 31523), |
| 60 | how do i transfer credits? | 0.967 | According to **doc_id 34013: Chemistry & Environmental Science**, students can look to tra |
| 62 | how to use registrar | 0.911 | To use the NJIT Registrar, you can access various forms and resources through their websit |
| 64 | i am f1 can i take online class? | 0.938 | Yes, as an F-1 international student at NJIT, you can take online classes. However, there  |
| 70 | forgot ucid password | 0.984 | According to the document titled "University Computing ID (UCID)" (<doc_id 39104: IST / Te |
| 72 | can i skip orientation at njit? | 0.982 | According to the New Jersey Institute of Technology document titled "New Student Orientati |
| 75 | how do i get transcript? | 0.993 | According to document **doc_id 31517 (Office of the Registrar)**, all current and former s |
| 76 | when is orientation? | 0.391 | According to doc_id 32043 (Fall 2025 - New Domestic Graduate Student Orientation), the Fal |
| 77 | where is registrar office? | 0.532 | According to doc_id 31578 (Winter 2016-2017 Academic Calendar) and doc_id 31577 (Winter 20 |
| 80 | how cpt applyy | 0.89 | To apply for Curricular Practical Training (CPT), follow these steps:  1. Meet with your a |
| 81 | last day for networking event | 0.954 | According to doc_id 33789 (Biomedical Engineering), the last day for networking events is  |
