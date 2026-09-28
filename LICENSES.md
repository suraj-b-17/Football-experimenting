# Data source licenses — read before publishing anything built from this repo

Every third-party data source used in this project is listed below with the
**actual terms**, quoted or accurately summarized from the primary source
(not assumed, not copied from a blog post). Read to the bottom before
deciding this is safe to publish — one source below is restrictive.

## Summary table

| Source | Used for | License | Public/portfolio use | Commercial use |
|---|---|---|---|---|
| StatsBomb Open Data | **Application data** (event data the animations are made from) | [StatsBomb Public Data User Agreement](#statsbomb-open-data) (proprietary, not open-source) | **Yes, with conditions** — research/analysis sharing is explicitly allowed; redistributing the raw data is not | **No** — explicitly prohibited |
| Metrica Sports sample data | Training (real tracking) | [Informal, no LICENSE file](#metrica-sports-sample-data) | Yes, credit requested | Not addressed — treat as research-only out of caution |
| DFL/IDSSE Bundesliga open data | Training (real tracking) | [CC BY 4.0](#dflidsse-bundesliga-open-data) | Yes | Yes, with attribution |
| SkillCorner Open Data | Evaluated as training data; **not in the shipped model** (no held-out gain, tested twice — see BENCHMARKS.md) | [MIT](#skillcorner-open-data) | Yes | Yes |

**Bottom line, stated plainly:** this project is safe to publish publicly as
a portfolio/research piece — sharing the code, the methodology, the write-up,
and screenshots/recordings of the animations. It is **not** safe to
commercially exploit (sell, monetize, put behind a paywall) anything derived
from the StatsBomb event data, and the raw StatsBomb JSON files themselves
must not be redistributed (bundled in this repo, re-uploaded elsewhere,
etc.) — only fetched live from StatsBomb's own GitHub repo, which is what
the loader in this project does. If commercial use is ever wanted, it would
require negotiating a separate commercial license with StatsBomb Services
Ltd — do not just proceed on the assumption that "open data" means
commercially free.

---

## StatsBomb Open Data

- **Source:** github.com/statsbomb/open-data (competition_id=2, season_id=27
  — Premier League 2015/16)
- **License document:** `LICENSE.pdf` in that repo — the "StatsBomb Public
  Data User Agreement," last updated 8 September 2023. Read in full (not
  skimmed) via PDF, quoted below.

**What it actually says (verbatim, section 1.2 — "The User may not"):**

> 1.2.1. edit, distort, distribute, reproduce, sell or in any way provide
> the data to any external or third party;
> 1.2.2. commercially exploit the data or any analysis derived from the use
> of the Service;
> 1.2.3. use the Service for any activity of an illegal or fraudulent
> nature, to violate any laws;
> 1.2.4. use the Service to produce, transfer, distribute or publish any
> material that might be defamatory or damaging to any individual or
> organisation
> 1.2.5. decompile, reverse engineer, or otherwise attempt to obtain the
> source code of the Services;

**Section 1.1** (what IS allowed): "StatsBomb will provide the User with
access to the Service to be used for **analysis, research and to facilitate
the shared ideas & understanding of the data**."

**Section 1.4** (attribution, mandatory, not optional): "The User is
required to accredit any publication of analysis formed from StatsBomb Data
with the **StatsBomb brand logo**."

**Section 7** (ownership): "The User acknowledges and agrees that all data
provided through the Service, is the property of StatsBomb."

### What this means for this project, plainly

- This is a **proprietary access agreement**, not an open-source or
  Creative Commons license, despite being called "open data."
- Publishing this project's **code, methodology, README, and rendered
  animations/screenshots** as a portfolio piece is squarely "analysis... to
  facilitate the shared ideas & understanding of the data" — permitted,
  **provided the StatsBomb brand logo is credited** wherever the analysis
  (i.e. the animations) is shown.
- **Do not commit the raw StatsBomb JSON files to this repo or any other
  redistribution channel.** Clause 1.2.1 prohibits redistributing/reproducing
  the data itself to a third party. The loader in this project fetches
  directly from `github.com/statsbomb/open-data` at run time and the data
  is cached locally outside the repo (see `paths.py`) — never committed,
  never re-hosted.
- **Do not sell, monetize, or otherwise commercially exploit** the
  animations, the derived reconstruction, or anything built from this data
  (clause 1.2.2) — this is an unambiguous "no," not a gray area.
- No 360 (freeze-frame) data exists for this competition/season — confirmed
  directly against `data/competitions.json`: `season_id=27`'s
  `match_available_360` is `null` and every match in
  `data/matches/2/27.json` has `"match_status_360"` of `"processing"` or
  `"scheduled"`, never `"available"`. This project uses only standard event
  data: event locations, carry/pass/shot end locations and durations, and
  StatsBomb's built-in **shot freeze frames** (named player positions at each
  shot), consistent with what's actually released. *Correction:* an earlier
  version of this file said shot freeze frames were used; at that time the
  code parsed them and then discarded them. They are used as anchors now.
- **Attribution in the viewer:** the viewer shows a "Data: StatsBomb Open
  Data" credit. Section 1.4 requires the **StatsBomb brand logo** on any
  publication of the analysis; add the official logo image next to that
  credit before publishing the animations anywhere.

## Metrica Sports sample data

- **Source:** github.com/metrica-sports/sample-data
- **License file:** none exists in the repo (checked directly: a request
  for `LICENSE` returns 404 both locally and against the upstream repo).
- **The only terms are in the README, under "Legal stuff"** (quoted in full,
  it is this short):

  > - Please be responsible with the use of this data.
  > - If you use it for anything public, please aknowledge the source.

- **What this means:** there is no formal open-source license, no explicit
  commercial permission or prohibition, and no redistribution clause.
  Treated here conservatively as **research/attribution-only, non-commercial
  by default** — the informal, good-faith tone of the README doesn't
  constitute a clear grant for commercial use, and this document said
  something similar should be told to the user plainly rather than assuming
  the best case: **if this project were ever monetized, Metrica's 3 sample
  matches should be re-verified with Metrica directly or dropped from the
  training pool first.** For the training-only role they play here (one of
  30 matches, none of their event/tracking data is ever displayed or
  redistributed, only used to fit model weights), this is used with
  attribution.

## DFL/IDSSE Bundesliga open data

- **Source used:** `kloppy`'s `sportec` loader, which pulls from
  `huggingface.co/datasets/pysport/idsse-data` — itself an explicit re-host
  of the authoritative record at **figshare, DOI
  10.6084/m9.figshare.28196177** ("An integrated dataset of spatiotemporal
  and event data in elite soccer," Bassek, Rein, Weber & Memmert, 2025,
  published alongside the *Scientific Data* paper
  doi.org/10.1038/s41597-025-04505-y).
- **License, read directly off the figshare record's own license field**
  (not inferred from the re-host's README): `{"name": "CC BY", "url":
  "https://creativecommons.org/licenses/by/4.0/"}` — **Creative Commons
  Attribution 4.0 International.**
- **What this means:** free to use, share, adapt, and use commercially,
  provided appropriate credit is given (author, source, license, and
  indication of changes) and no additional restrictions are applied. No
  non-commercial or share-alike clause. Data owner: Deutsche Fußball Liga
  (DFL); underlying collection by Sportec Solutions (TRACAB optical
  tracking).
- 7 matches (2 top-flight + 5 second-division, 2022/23 Bundesliga season).

## SkillCorner Open Data

- **Source:** github.com/SkillCorner/opendata
- **License file, quoted in full (it is the standard MIT text):**

  > The MIT License
  >
  > Copyright (c) 2020 SkillCorner. https://skillcorner.com
  >
  > Permission is hereby granted, free of charge, to any person obtaining a
  > copy of this software and associated documentation files (the
  > "Software"), to deal in the Software without restriction, including
  > without limitation the rights to use, copy, modify, merge, publish,
  > distribute, sublicense, and/or sell copies of the Software, and to
  > permit persons to whom the Software is furnished to do so, subject to
  > the following conditions: [...] THE SOFTWARE IS PROVIDED "AS IS",
  > WITHOUT WARRANTY OF ANY KIND [...]

- **What this means:** the repository (including the `data/` directory
  containing the tracking/event JSON and CSV files) is licensed under MIT —
  one of the most permissive open-source licenses, explicitly permitting
  commercial use, modification, and redistribution, with only a requirement
  to retain the copyright notice. The README additionally asks (not
  requires) that users credit SkillCorner if the data is used publicly.
- 20 matches (2024/25 Australian A-League) actually have tracking data
  committed (the README's "10 matches" is stale — verified directly against
  the repo's `data/matches/` directory listing, which has 20 match folders,
  each containing a tracking file). Tracking is "broadcast tracking" —
  computer-vision-derived from TV footage, so players outside the camera's
  view are either missing or filled in by SkillCorner's own extrapolation
  model (flagged `is_detected: false` in the data). This project's loader
  (`training/loader_skillcorner.py`) uses only frames flagged as genuinely
  detected as training targets — extrapolated positions are model output,
  not ground truth, and using them to train another model would be training
  on a black box's guesses, not real tracking data.

## Additional sources considered and rejected

Per the instruction to verify (not assume) any additional open tracking
dataset before use, the following were checked and are **not** included,
with the reason:

- **PFF FC 2022 World Cup dataset** (broadcast tracking, 64 matches): no
  license or terms-of-use text exists on the public request page
  (`blog.fc.pff.com/blog/pff-fc-release-2022-world-cup-data`) — access is
  gated behind a request form whose actual terms weren't visible without
  registering. Excluded: "the bar is a clear, verifiable open license," and
  this doesn't have one visible without an account.
- **Friends-of-Tracking-Data "Last Row"** (hand-collected Liverpool goal
  sequences): no LICENSE file in the repo (confirmed: 404). Excluded for
  lacking any clear license, and also too small/narrow (19 goal sequences,
  not full matches) to be a meaningful training addition.
- **Alfheim/Tromsø IL (Simula datasets)**: described as "free for research,
  citation required" — not continuous player tracking in the sense used
  here in a verified, redistributable-for-training way, and predates
  verification to the same bar as the others; not pursued given the three
  primary sources plus SkillCorner already total 30 real matches, a
  substantial pool.
- **SoccerMon (Zenodo, CC BY 4.0)**: real license, but the position data is
  **GPS**, not optical/broadcast tracking — far lower spatial precision and
  a fundamentally different measurement modality (GPS units on players, not
  camera-derived x/y). Doesn't meet "genuine frame-by-frame player
  tracking" at the fidelity the other sources provide; excluded on data-
  quality, not licensing, grounds.

## What is NOT in this project

WhoScored/Opta event-scraped data (used in an earlier, separate version of
this project) has been completely removed — no code, cached files, or
documentation referencing it remain in this repo (see the root README's
"What changed" section for the removal verification).
