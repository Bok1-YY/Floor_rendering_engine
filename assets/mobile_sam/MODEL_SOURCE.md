# Model provenance

Downloaded on 2026-07-20 for offline CPU inference.

- Upstream architecture and weights: [ChaoningZhang/MobileSAM](https://github.com/ChaoningZhang/MobileSAM), Apache-2.0.
- Pre-exported ONNX repository: [Acly/MobileSAM](https://huggingface.co/Acly/MobileSAM), model card marked MIT. Its encoder export includes resize-independent preprocessing; the decoder follows the official SAM ONNX prompt interface.
- Encoder URL: `https://huggingface.co/Acly/MobileSAM/resolve/main/mobile_sam_image_encoder.onnx`
- Decoder URL: `https://huggingface.co/Acly/MobileSAM/resolve/main/sam_mask_decoder_multi.onnx`

SHA-256:

```text
580f5fb648ea1062c0aabc26217aed56921985f03f0cbbd852bba81d760cc749  mobile_sam_encoder.onnx
8976b90a87ba50a6a72217a5ff994f7d25ce16f2229fcc1ed259e1294c622ffe  mobile_sam_decoder.onnx
```

The runtime does not download weights. Missing or invalid model files cause a
visible manual-paint fallback; they never cause an online upload or a global
colour correction fallback.
