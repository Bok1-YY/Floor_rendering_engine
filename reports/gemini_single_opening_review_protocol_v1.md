# Gemini single-opening review protocol v1

This protocol is advisory only. It may not mutate the source contract, promote
semantic status, create an entrance root, or authorize Blender/IFC. The local
geometry and provenance validators remain authoritative.

## Request contract

Send exactly one opening per request, with two images only:

1. the canonical full plan with the target opening highlighted;
2. a registered crop containing the target opening and its candidate wall.

The text payload must contain only the target ID and immutable facts. Do not ask
the model to infer coordinates, rotate the image, repair the registration, or
review other openings. Use a small output budget and disable optional sampling
parameters unsupported by the selected Gemini model (`temperature`, `topP`, and
`topK` are omitted for Gemini 3 models).

## Compact prompt

```text
You are a visual reviewer, not a CAD author. Review ONLY opening {opening_id}.
The images are canonical and north-up; do not rotate, mirror, or invent geometry.
The blue segment and wall atom are supplied facts, not claims to accept.
Use pixels visible in the images only. If a field is not directly visible,
return null. If any image and supplied fact conflict, set review_status to
"conflict". Return ONE minified JSON object, no markdown and no explanation:
{"opening_id":"{opening_id}","review_status":"agree|conflict|indeterminate","visual_kind":"door|window|portal|glazed_interface|wall_gap|unknown|null","swing":"in|out|left|right|none|unknown|null","side_a":"known|unknown|null","side_b":"known|unknown|null","confidence":"high|medium|low|null"}
Rules: never name a room; never assert an entrance; never estimate width/height;
never convert a dashed swing arc into a confirmed door; never use labels outside
the crop; one opening only.
```

`side_a` and `side_b` are deliberately coarse visual-presence flags, not room
identities or adjacency facts. Room names, host wall ownership, traversability,
width, jambs, height, and entrance/root decisions must be resolved locally from
registered geometry and independently audited evidence.

## Local acceptance rules

The response is usable only if all of these hold:

- HTTP response is successful and the response is complete, not truncated;
- JSON parses and has exactly the seven keys above;
- `opening_id` equals the requested ID;
- all enum values are in the prompt vocabulary;
- no additional text or fields are present;
- the target is present in both supplied images;
- the registration validator passes at the configured pixel tolerance;
- the supplied source, crop, overlay, and geometry hashes match the candidate;
- `review_status=conflict` or `indeterminate` forces unresolved status;
- `review_status=agree` is still advisory and cannot promote an opening alone.

For a semantic promotion candidate, require agreement with the independent
geometry audit, opening-to-wall-space evidence, adjacency/root audit, and a
second independent reviewer. Gemini output is an observation layer, never the
source of truth.

## Retry policy

Retry at most once, with the same images and prompt, after a transient transport
error. Do not retry truncation using a larger prompt or a larger schema. A second
parse failure is recorded as `advisory_failed`; the gate remains closed and the
pipeline proceeds with deterministic evidence work.
