# Floor_engine_Linux development handoff

Captured: 2026-08-19 Asia/Singapore

## Start here

1. Read `docs/CODEX_BLENDER_360VR_IMPLEMENTATION_HANDOFF.md` completely.
2. Read `HANDOFF/SOURCE_ENVIRONMENT.txt` and `HANDOFF/GIT_STATUS.txt`.
3. Do not reset the dirty worktree.
4. Recreate `.venv` and `web/node_modules` on the new computer.
5. Configure API keys through the app settings; secrets were intentionally excluded.
6. Install a pinned Blender LTS runtime on the new computer.

## Package facts

- File count: 6127
- Uncompressed size MiB: 1772.07
- Git HEAD: 54c9e51736f677fbe93a23daa2b8e606058851d5
- Main plan SHA-256: A6945D09620E8A9D79A26DB34DD1F235DC0A75193554D9C8FA7977499CA8A8BE
- Includes local ONNX models: yes
- Includes portable .NET / ACadSharp support: yes
- Includes external geometry datasets: yes
- Includes selected floor/VR regression evidence: yes
- Includes Blender runtime: no; source machine did not have Blender installed
- Includes API keys or browser sessions: no

## Integrity

Use `HANDOFF/FILES_SHA256.csv` after extraction. The archive-level SHA-256 is stored beside the ZIP.
