# Ensora+ — Project context

> Learn smarter, easier and better, together.

## Mission [REQUIREMENT]
Modernize the ENSAI Ensora tutoring club's shared Drive into a local-first, intelligent, adaptive web workspace. Students currently reconcile Moodle teaching materials and assignments, Ensora historical exams and solutions, and Pamplemousse timetables manually. They also switch between generic AI tools, calendars, notes and memorization apps. Ensora+ connects these workflows without replacing source platforms, teachers or peer tutoring.

## Product vision
The subject is the canonical join between current curriculum, historical resources, assessments, tasks, concepts and student history. A new assessment initiates curriculum identification, resource matching and a proposed schedule. Starting a session retrieves or generates appropriate materials; measured outcomes update mastery and future activities. The product is not simply a chatbot or file browser.

Primary users: ENSAI 1A, 2A and 3A students, including specialization-specific curricula. Secondary stakeholders: Ensora club, instructors and school IT; later peer contributors. Keep academic content and source notation intact, support French and English, and retain student control over connections, scheduling and model usage.

## Success and boundaries
- Reduce manual resource discovery and planning; demonstrate an end-to-end feedback loop, not disconnected screens.
- Read only authorized school resources; preserve original platforms and personal history. Historical exams inform practice, not the official syllabus.
- AI assists explanations and practice; mastery is an estimate based on evidence, never a guarantee of a grade.
- Local persistence and reuse are required; external inference is optional and requires explicit permission.
- Full scope includes multimodal study tools and community. MVP is a separately prioritized subset ([MVP](MVP.md)).

## Evidence and provenance
S1: user-supplied `Texte collé(20261127-122103).txt`, French LaTeX specification, 1,053 lines; primary baseline, sections 1–12 (project through continuous learning loop).
S2: prior `Texte collé (6).txt`, detailed technical specification, sections 1–30; supplemental requirements, examples and proposed technologies. S3: prior `Texte collé (4)(9).txt`, functional/technical proposal including validation and roadmap. S4: `Ensora_plus_cahier_des_charges.ipynb`, prior design notebook; retrieved excerpts clarify source screenshots and limitations. S5: public [repository](https://github.com/Fan-y-h/hackathon_ENSORA), observed 2026-11-27, main with one commit, .gitignore, LICENSE, README.md. No local working tree or unpushed changes inspected; clone attempt failed in this environment. The notebook was not executed. The separate official event rules, judging rubric and confirmed duration were not available; S2 includes a proposed project brief and illustrative 48-hour schedule, not confirmed organizer rules.

S1 prevails when requirements differ; earlier scope supplements it (especially Community and account management). Technical choices below are proposed, not pre-existing code. [DECISIONS](DECISIONS.md) records conflicts and open questions. No live connector or provider access was tested.

## Reading order
Start with [REQUIREMENTS](REQUIREMENTS.md), [ARCHITECTURE](ARCHITECTURE.md), [DATA_MODEL](DATA_MODEL.md), [API_CONTRACTS](API_CONTRACTS.md), [MVP](MVP.md), [AGENT_TEAM](AGENT_TEAM.md) and [BACKLOG](BACKLOG.md). Follow the domain documents for each task. [PROJECT_STRUCTURE](PROJECT_STRUCTURE.md) is target, not current state.

## Scope amendment S4 (2026-11-27)
User-supplied Grade Tracker & Academic Optimizer specification (`Texte collé(20261127-131527).txt`, sections 1–12) extends the academic feedback loop: what to learn, what grades are needed, what subjects to prioritize and how to allocate study time. All original requirements remain. Academic records, objectives, simulation and planner integration are P0 in S4; source access and ENSAI validation/compensation rules remain unverified. See [GRADE_TRACKER](GRADE_TRACKER.md).
