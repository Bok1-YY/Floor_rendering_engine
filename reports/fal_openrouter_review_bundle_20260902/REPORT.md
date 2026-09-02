# fal OpenRouter Gemini opening-review bundle — 2026-09-02

Official endpoint: `openrouter/router/vision`, model `google/gemini-2.5-flash`.

- Coverage: OP001–OP011, exactly once per accepted result.
- Input per opening: registered full overlay plus registered crop overlay.
- Bundle candidate hash: `6193c1f2f25e95ed77416ed4ff18a89492e64d23d0eb72660d0351365d4e4323`.
- Bundle file SHA-256: `8cf970821d54aa79be4dc69fa6443b8052196f5ae1422221e132fc95f6782a3e`.
- Total charged cost reported by fal: `$0.0037274`.
- All 11 results passed the strict seven-field JSON validator and image-byte binding.

Visual advisory summary:

| opening | visual kind | swing | side A / side B | confidence |
|---|---|---|---|---|
| OP001 | door | right | unknown / unknown | high |
| OP002 | door | left | known / known | high |
| OP003 | door | right | unknown / known | high |
| OP004 | door | left | known / known | high |
| OP005 | door | right | unknown / unknown | high |
| OP006 | door | right | known / known | high |
| OP007 | door | out | known / known | high |
| OP008 | door | right | known / known | high |
| OP009 | door | out | known / known | high |
| OP010 | glazed_interface | none | known / known | high |
| OP011 | door | out | known / known | high |

These are advisory pixel observations. They do not select room identities, prove a host/effective void/jamb, establish traversability, override rejected history, create an exterior root, change score, or authorize Blender/IFC. In particular, OP011's visual `door` response conflicts with its unresolved source classification and remains blocked.

`semantic_promotion=false`  
`score_effect=none`  
`build_authorized=false`  
`ready=false`
