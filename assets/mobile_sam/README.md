# MobileSAM offline assets

This directory is bundled into the Windows executable for local floor-mask
segmentation. Runtime inference uses ONNX Runtime on CPU; no image or embedding
is uploaded to a third-party segmentation service.

Expected files:

- `mobile_sam_encoder.onnx`
- `mobile_sam_decoder.onnx`
- `LICENSE-MobileSAM`
- `MODEL_SOURCE.md`

The model is derived from the official MobileSAM project and distributed under
its Apache-2.0 license. See `MODEL_SOURCE.md` for the exact source and hashes.
