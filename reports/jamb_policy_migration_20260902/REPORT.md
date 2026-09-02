# Jamb-policy migration: wall thickness 120 mm → governing support minimum 50 mm

Authoritative policy: `opening_contract.minimum_jamb_support_m = 0.05 m` in the current v2.1 source. Wall atom `thickness_m = 0.12 m` remains a geometry dimension and is no longer used as the candidate jamb threshold.

Classification changes:

- OP003: `0 m`, remains insufficient.
- OP004: `0.879937 m`, sufficient.
- OP006: `0.094538 m`, changes from insufficient to sufficient.
- OP007: `0.06545 m`, changes from insufficient to sufficient.
- OP008: `0.035139857 m`, remains insufficient.
- OP009: `1.44905 m`, sufficient.
- OP010: `2.166257 m`, sufficient.

Important old → new candidate hashes:

| Artifact | Old | New |
|---|---|---|
| OP003 packet | `9ca0032f...` | `29310d3d...` |
| OP005/006 packet | `590433f7...` | `ce7b033d...` |
| OP007/008 packet | `873c2d87...` | `d6c00d7e...` |
| OP009/010 packet | `c4ccbf3f...` | `766b3afe...` |
| targeted evidence | `e8c906b6...` | `12c94e9c...` |
| partition-targeted evidence | `e191c949...` | `a064c9da...` |
| OP004/009 review bundle | `77b7fbce...` | `962e3cb3...` |
| OP002/006/007 review bundle | `30eae447...` | `e6ca564a...` |
| OP004/009 correction wrapper | `0043b67b...` | `09d7bdc3...` |
| OP002 correction wrapper | `a2b86837...` | `c3fcb758...` |
| correction registry | `444e271b...` | `2d776f3a...` |

New active file hashes:

- targeted evidence: `7eace845e4310ad0524455f4f63c0e72e1f2af8bb0115fddab04c00eced62e13`
- partition-targeted evidence: `3a6ac6ddd89cc54fd13f6cc80bbd614e62c316e1dc0d5d04da9caa0d48c785ab`
- OP004/009 review bundle: `6f892961c25575bfbe5dada827e6202d25f908df2af0acc11ff917d5198e5d46`
- OP002/006/007 review bundle: `46614410e986e9333edca7044e8a0786b36e54c9b7571f18076c860f54bbebcf`
- OP004/009 correction wrapper: `f9cffb7389ec874a8a289c22c44a92394bad10eb4982f78a7f979793969f7623`
- OP002 correction wrapper: `ff5d3b1c2d0957653c314f65a87170471c732730dace9d2e0c45254dd2002bca`
- correction registry: `efbfecf27c3e0556485ed9852ee7aa10c9acfec315076c926a74067df7869648`
- live gate report: `91cd797a9d060a1397175ff6b7c7fe4d021b6872949cd44da326d5c3221ff282`

Historical verifier reports with 120 mm threshold/hashes remain audit history but are superseded for active jamb classification by this migration and its forthcoming independent verifier.

The source document is unchanged. Source score remains `65/100`, with S06/S07/S08 failing; no pair, application, semantic, score, Blender, or IFC authorization is created by correcting the jamb policy.
