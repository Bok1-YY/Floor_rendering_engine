# Third-party notices

Floor AI includes and depends on third-party software and model assets. Those
components remain under their respective licenses; the project's
`AGPL-3.0-only` license does not replace their terms.

## MobileSAM architecture and weights

- Project: [ChaoningZhang/MobileSAM](https://github.com/ChaoningZhang/MobileSAM)
- License: Apache License 2.0
- Local license copy: [`assets/mobile_sam/LICENSE-MobileSAM`](assets/mobile_sam/LICENSE-MobileSAM)

The bundled MobileSAM encoder is derived from the official MobileSAM
architecture and weights. The Apache-2.0 copyright, patent, attribution, and
redistribution conditions continue to apply to this component.

## Acly MobileSAM ONNX exports

- Project: [Acly/MobileSAM](https://huggingface.co/Acly/MobileSAM)
- License declared by the model repository: MIT
- Bundled files: `mobile_sam_encoder.onnx`, `mobile_sam_decoder.onnx`

The ONNX export repository identifies these exports as MIT-licensed. Exact
source URLs, download date, and SHA-256 hashes are recorded in
[`assets/mobile_sam/MODEL_SOURCE.md`](assets/mobile_sam/MODEL_SOURCE.md).

Copyright (c) Acly

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Depth Anything V2 Small

- Project: [DepthAnything/Depth-Anything-V2](https://github.com/DepthAnything/Depth-Anything-V2)
- Bundled representation: `assets/depth_anything_v2/depth_anything_v2_small.onnx`
- License: Apache License 2.0 for the Small model

Depth Anything V2 Small is used only for offline relative-depth and occlusion
edge analysis in panorama floor masking. Source, conversion and checksum
details are recorded in
[`assets/depth_anything_v2/MODEL_SOURCE.md`](assets/depth_anything_v2/MODEL_SOURCE.md).

## CLIPSeg rd64 refined

- Base model: [CIDAS/clipseg-rd64-refined](https://huggingface.co/CIDAS/clipseg-rd64-refined)
- ONNX distribution: [Xenova/clipseg-rd64-refined](https://huggingface.co/Xenova/clipseg-rd64-refined)
- Model-card license declaration: Apache License 2.0

The quantized ONNX graph is used only for offline open-vocabulary floor
semantics. Exact model/tokenizer checksums and provenance are recorded in
[`assets/clipseg/MODEL_SOURCE.md`](assets/clipseg/MODEL_SOURCE.md).

## Application dependencies

The Python and JavaScript dependencies are distributed under their own
licenses. Their pinned package names and versions are recorded in
`requirements.txt`, `requirements-dev.txt`, and `web/package-lock.json`.
Notable packaged components include Sharp and its libvips binaries, whose
package metadata records Apache-2.0, MIT, and/or LGPL-3.0-or-later terms
depending on the target package.

Redistributors are responsible for preserving the notices and satisfying the
license terms of the exact dependency artifacts they distribute.
