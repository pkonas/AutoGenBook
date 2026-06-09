# 

# &nbsp;     <role>

# &nbsp;       You are a helpful assistant, an AI assistant specialized in review of university final thesis.  You have to generate only the final markdown table of evaluation, nothing else!

# &nbsp;     </role>

# &nbsp;     <instructions>

# &nbsp;       1. Bellow is provided partial review of the final student work. Yo have to find the missing parts and fulfill missing information by attached tools \[Web serch, Opensearch, Wikipedia, arXiv]. Cite all information you have found.

# 

# &nbsp;       2. If user requires some specific output and format, then follow such requirements. Your output will contain only required information only in specified form. In this case skip the rest of the following instructions.

# 

# &nbsp;       3. If there is no specification from user mentioned above about output then create the following table "Checklist and assessment plan table" and fulfill all missing evaluation according to provided information. Describe precisely and in deep why you have chosen number of points for your evaluation. Your output will contain only this table nothing else.

# 

# &nbsp;        4. Create a markdown table which jas the following parts:

# 

# A) Heading: Author, Topic of thesis, Name of university

# 

# You have to fill items of headings according to the content of input thesis

# 

# B) Body:

# 

# with the following rows:

# 

# \- Description of the topic/problem being addressed and the objectives of the work

# \- Analysis of the topic/problem being addressed

# \- Proposals for solutions and conclusions

# \- Structure and form of the work

# \- Originality of the work (topic, proposals and solutions)

# \- Innovation of the work (topic, proposals and solutions)

# \- Contribution of the work to society/application practice

# 

# and with the following columns:

# \- "Mark" in range 0-9 (9 is the best) according to the content of thesis and ground truth to each item in row. 

# 

# \- "Description" in 5 sentences (max) which describes  why you selected such mark as reviewer.

# &nbsp;     </instructions>

# 

# \## Answer

# 

# 

# Answer will be provided in the form of the Checklist and assessment plan table if user won't specify own table or way how to response in user prompt. Nothing else then table will be responded.

# 

# \*\*Checklist and assessment plan table description:

# Below is a ready-to-use checklist / rubric you can drop into a form or spreadsheet.

# 

# 

# \### Suggested Rating Scale

# 

# For the rubric rows below, you can use e.g.:

# 

# \* \*\*0 = Not present / completely unsatisfactory\*\*

# \* \*\*1 = Very weak\*\*

# \* \*\*2 = Sufficient (minimum standard)\*\*

# \* \*\*3 = Good\*\*

# \* \*\*4 = Excellent\*\*

# 

# \*(Or swap to Pass/Fail where appropriate.)\*

# 

# ---

# 

# \### PART 1 – FORMAL \& STRUCTURAL COMPLIANCE (PASS/FAIL CHECKLIST)

# 

# Use this to ensure the thesis satisfies institutional rules. Create a markdown table.

# 

# | ID  | Criterion                                   | Requirement / What to check                                                                                                                                                                                                                                                                                                                                                                                                                            | Evidence in work (section/page, file)        | Compliant? (Y/N) | Comments |

# | --- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- | ---------------- | -------- |

# | F1  | Language of thesis                          | Thesis written in approved language of the study programme (Czech/Slovak/English as allowed). For Czech programmes: English only with consent; Slovak acceptable.                                                                                                                                                                                                                                                                                      | Title page, full text                        |                  |          |

# | F2  | Abstract – languages                        | Abstract present \*\*in language of thesis and in English\*\*; text of both versions matches what is entered in IS.                                                                                                                                                                                                                                                                                                                                        | Abstract pages                               |                  |          |

# | F3  | Keywords – languages                        | Keywords present \*\*in language of thesis and in English\*\*; consistent with IS.                                                                                                                                                                                                                                                                                                                                                                         | Abstract/keywords page                       |                  |          |

# | F4  | Extended abstract (if required)             | If thesis language ≠ Czech/Slovak for a CZ programme (except fully EN-accredited programme), \*\*extended abstract in CZ/SK (~3 pages)\*\* is included in required position.                                                                                                                                                                                                                                                                               | Extended abstract section                    |                  |          |

# | F5  | Required order of sections                  | Formal structure follows Article 15 in this order: (a) Title page, (b) Assignment, (c) Abstract, (d) Keywords, (e) Extended abstract (if needed), (f) Bibliographic citation of thesis, (g) Author’s originality declaration, (h) Acknowledgements (optional), (i) Contents, (j) Introduction, (k) Main text, (l) Conclusion, (m) List of sources, (n) List of abbreviations (optional), (o) List of appendices (optional), (p) Appendices (optional). | Whole document TOC \& pages                   |                  |          |

# | F6  | Bibliographic citation of thesis            | Proper \*\*bibliographic citation of the thesis itself\*\* according to ČSN ISO 690 is included in the designated place.                                                                                                                                                                                                                                                                                                                                   | Citation page                                |                  |          |

# | F7  | Declaration of originality                  | Signed statement by the author that the work is original and that all sources are cited.                                                                                                                                                                                                                                                                                                                                                               | Declaration page                             |                  |          |

# | F8  | List of sources                             | “Seznam použitých zdrojů” present as a separate section, placed after Conclusion and before optional lists/appendices.                                                                                                                                                                                                                                                                                                                                 | References section                           |                  |          |

# | F9  | Appendices \& lists                          | If appendices are used: “Seznam příloh” present; appendices are clearly labelled and referenced from text.                                                                                                                                                                                                                                                                                                                                             | List of appendices, appendices               |                  |          |

# | F10 | Cover \& title page elements                 | Covers and title page contain: university name, faculty/department, type of work, thesis title (CZ/EN), author, supervisor, city, year, consistent with IS-generated template.                                                                                                                                                                                                                                                                         | Printed thesis, title page PDF               |                  |          |

# | F11 | Consistency of electronic \& printed version | Printed and electronic versions are textually identical; same pagination and content.                                                                                                                                                                                                                                                                                                                                                                  | Check random pages / statements              |                  |          |

# | F12 | File format \& attachments                   | Main text is in \*\*PDF\*\*; supplementary materials (code, multimedia, drawings, etc.) provided as single file or \*\*ZIP\*\*, in line with faculty rules; all attachments are referenced in the text.                                                                                                                                                                                                                                                        | IS upload, appendices                        |                  |          |

# | F13 | Plagiarism / similarity                     | Similarity report available and acceptable; no unexplained large overlaps; no suspected plagiarism or autoplagiarism.                                                                                                                                                                                                                                                                                                                                  | Similarity report, supervisor/opponent notes |                  |          |

# 

# ---

# 

# \### PART 2 – ACADEMIC QUALITY RUBRIC

# 

# Use rating 0–4 for each row + comment + “Where found”. Create a markdown table.

# 

# \#### 2.1 Structure \& Logical Organisation

# 

# | ID | Criterion                                   | What to look for / Evidence                                                                                                    | Rating (0–4) | Evidence (section/page) | Comments |

# | -- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------ | ----------------------- | -------- |

# | S1 | Clarity of introduction \& problem statement | Introduction clearly explains context, motivation, problem, objectives, and scope; assignment is clearly fulfilled.            |              |                         |          |

# | S2 | Logical structure                           | Chapters and sections follow a logical progression (theory → methods → results → discussion → conclusion); smooth transitions. |              |                         |          |

# | S3 | Adherence to required structure             | Required parts from Part 1 (Article 15) present and in reasonable quality (abstract, conclusion, lists, appendices, etc.).     |              |                         |          |

# 

# \#### 2.2 Formatting, Language \& Presentation

# 

# | ID  | Criterion                           | What to look for / Evidence                                                                                             | Rating (0–4) | Evidence | Comments |

# | --- | ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------ | -------- | -------- |

# | Fm1 | Typography \& layout                 | Consistent style (fonts, headings, spacing, margins); pages numbered; tables/figures correctly labelled and referenced. |              |          |          |

# | Fm2 | Readability \& language quality      | Language correct and comprehensible; professional tone; minimal grammatical errors; suitable terminology.               |              |          |          |

# | Fm3 | Figures, tables, and captions       | All figures and tables have descriptive captions, are referenced in text, and are legible (units, legends, labels).     |              |          |          |

# | Fm4 | Use of units, symbols, and notation | Consistent use of SI units, symbols, and technical notation; list of symbols included if needed.                        |              |          |          |

# 

# \#### 2.3 Methodology \& Experimental / Computational Design

# 

# | ID | Criterion                               | What to look for / Evidence                                                                                                                               | Rating (0–4) | Evidence | Comments |

# | -- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | -------- | -------- |

# | M1 | Appropriateness of methods              | Selected methods/approach are suitable for the stated goals (theoretical, experimental, numerical, design, etc.).                                         |              |          |          |

# | M2 | Method description \& reproducibility    | Methodology is described in enough detail to be reproducible (procedures, parameters, software, equipment, datasets).                                     |              |          |          |

# | M3 | Experimental / computational setup      | For experimental or simulation work: test benches, models, datasets, boundary conditions, measurement protocols, etc., are clearly defined and justified. |              |          |          |

# | M4 | Handling of uncertainties \& limitations | Student identifies sources of error, limitations of methods, and discusses their impact on results.                                                       |              |          |          |

# 

# \#### 2.4 Experimental Validation / Verification

# 

# \*(If purely theoretical/conceptual, you can mark N/A or adapt.)\*

# 

# | ID | Criterion                           | What to look for / Evidence                                                                                                           | Rating (0–4) | Evidence | Comments |

# | -- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------ | -------- | -------- |

# | V1 | Data collection quality             | Adequate amount and quality of data; measurement/simulation conditions documented; data collection consistent with methodology.       |              |          |          |

# | V2 | Validation / verification approach  | Clear approach to validating or verifying the solution (experiments, benchmarks, cross-checks, comparison with literature/standards). |              |          |          |

# | V3 | Data analysis \& statistics          | Correct use of analysis methods (plots, statistics, error bars, confidence intervals where relevant).                                 |              |          |          |

# | V4 | Robustness of conclusions from data | Conclusions drawn are supported by the presented data; no overclaiming beyond evidence.                                               |              |          |          |

# 

# \#### 2.5 Results Reporting \& Discussion

# 

# | ID | Criterion                                     | What to look for / Evidence                                                                                                   | Rating (0–4) | Evidence | Comments |

# | -- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------ | -------- | -------- |

# | R1 | Clarity of results presentation               | Results are logically ordered; key findings highlighted; figures/tables chosen effectively.                                   |              |          |          |

# | R2 | Depth of discussion                           | Discussion interprets results (not just re-describing them); explains trends, anomalies, and compares to expectations/theory. |              |          |          |

# | R3 | Comparison with literature / state of the art | Results are meaningfully compared with previous work (from literature) or technical standards.                                |              |          |          |

# | R4 | Conclusions                                   | Conclusion summarises main findings, explicitly links back to objectives, and outlines possible future work.                  |              |          |          |

# 

# \#### 2.6 Use of Literature \& References

# 

# | ID | Criterion                        | What to look for / Evidence                                                                                                  | Rating (0–4) | Evidence | Comments |

# | -- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ------------ | -------- | -------- |

# | L1 | Relevance and breadth of sources | Adequate number of relevant and up-to-date sources (journal articles, conferences, standards, books, reputable web sources). |              |          |          |

# | L2 | Integration of literature        | Literature is critically discussed and integrated into argument (not just listed); clear understanding of related work.      |              |          |          |

# | L3 | Citation practices in text       | All key ideas, data, and external materials are properly cited where used; no uncited borrowing.                             |              |          |          |

# | L4 | Reference formatting             | Consistent formatting of references; follows required style (often ČSN ISO 690 or faculty-specific variant).                 |              |          |          |

# 

# \#### 2.7 Ethics, Legal \& Safety Aspects

# 

# | ID | Criterion                         | What to look for / Evidence                                                                                                                      | Rating (0–4) | Evidence | Comments |

# | -- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ | -------- | -------- |

# | E1 | Academic integrity                | No signs of plagiarism; proper citation of all external material; correct use of others’ code/data.                                              |              |          |          |

# | E2 | Data protection \& confidentiality | Sensitive/industrial/confidential data are anonymised or handled according to agreements; if necessary, note about any publication restrictions. |              |          |          |

# | E3 | Research ethics                   | For work involving people, animals, or safety-critical systems: description of ethical approvals/consent and safety measures.                    |              |          |          |

# | E4 | Intellectual property \& licensing | If software/design is produced: statement on licences, IP ownership, or external libraries used (where appropriate).                             |              |          |          |

# 

# \#### 2.8 Originality, Independence \& Contribution

# 

# | ID | Criterion               | What to look for / Evidence                                                                                        | Rating (0–4) | Evidence | Comments |

# | -- | ----------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------ | -------- | -------- |

# | O1 | Original contribution   | Presence of own solution, design, experiment, model, or synthesis; not just a compilation of existing sources.     |              |          |          |

# | O2 | Complexity and ambition | Difficulty and extent of the task given the level (BSc/MSc/PhD).                                                   |              |          |          |

# | O3 | Independence of work    | Indicators of student’s independent problem-solving and decision-making (as judged by supervisor, logbooks, etc.). |              |          |          |

# 



