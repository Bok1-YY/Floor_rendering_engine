# OP002 human review card

Status: `pending_human_review`. This card does not promote source facts.

Visual evidence:

- [Registered full overlay](C:/Users/1_1/Desktop/Floor_engine_GoalLoopV2_20260831/reports/op002_vertical_evidence_20260901/op002-vertical-full-overlay.png)
- [Registered crop](C:/Users/1_1/Desktop/Floor_engine_GoalLoopV2_20260831/reports/op002_vertical_evidence_20260901/op002-vertical-crop-overlay.png)

Candidate under review:

```text
opening: OP002
kind: hinged door
host: ATOM-WB006-02
pair: bedroom_01 ↔ bedroom_corridor
traversable: yes
```

Evidence summary:

- registration error: `0 px`;
- target-aware physical cut connects `bedroom_01` to the public component;
- topology closure restores `bedroom_01` as a separate room face;
- `bath` remains isolated;
- Gemini 3.6 through the configured 7897 route returned complete agreement;
- independent verifier accepted the packet only as unresolved candidate.

Human decisions required:

```text
Q1 geometry marker matches the visible door: yes / no
Q2 opening is a hinged door: yes / no / unclear
Q3 bedroom_01 is one side: yes / no / unclear
Q4 bedroom_corridor is the other side: yes / no / unclear
Q5 opening is traversable: yes / no / unclear
```

Even an all-yes human response must be bound to reviewer identity/timestamp and
independently applied before source, score, adjacency, or build state changes.
