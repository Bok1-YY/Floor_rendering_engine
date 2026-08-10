"""Desktop and command-line front ends for the standalone colour calibrator."""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path

from PIL import Image, ImageTk

try:  # Package import (tests / ``python -m``).
    from .engine import (
        MatchReport, build_sample_match_plan, match_sample_color, open_image, save_image,
    )
except ImportError:  # Direct script / double-click launch.
    from engine import (
        MatchReport, build_sample_match_plan, match_sample_color, open_image, save_image,
    )


IMAGE_TYPES = [
    ("图片", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
    ("所有文件", "*.*"),
]


def _parse_rect(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("选区格式应为 x,y,w,h") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("选区格式应为 x,y,w,h")
    return values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="大面积新拍样品图匹配旧小样颜色")
    parser.add_argument("--source", help="新拍的大面积样品图")
    parser.add_argument("--reference", help="旧的小面积参考图")
    parser.add_argument("--output", help="输出图片")
    parser.add_argument(
        "--rect", type=_parse_rect, default=(0, 0, 1, 1),
        help="新图取色选区，归一化 x,y,w,h；默认整图",
    )
    parser.add_argument("--strength", type=float, default=0.85, help="强度 0~1")
    parser.add_argument(
        "--match-luminance", action="store_true",
        help="同时匹配明暗；默认保留新图的纹理和光影",
    )
    parser.add_argument(
        "--algorithm", choices=("classic", "distribution"), default="classic",
        help="校色算法：classic 经典快速，distribution 精细分布匹配",
    )
    parser.add_argument(
        "--illumination", choices=("off", "chroma", "full"), default="off",
        help="空间光照校正：off 关闭，chroma 仅校正色偏，full 同时校正明暗",
    )
    return parser


def run_cli(args: argparse.Namespace) -> int:
    missing = [name for name in ("source", "reference", "output") if not getattr(args, name)]
    if missing:
        raise SystemExit("命令行模式缺少参数: " + ", ".join("--" + name for name in missing))
    source_path = Path(args.source)
    algorithm = "distribution" if args.illumination != "off" else args.algorithm
    with Image.open(source_path) as opened:
        source_info = dict(opened.info)
    result, report = match_sample_color(
        open_image(source_path),
        open_image(args.reference),
        source_rect=args.rect,
        strength=args.strength,
        preserve_luminance=not args.match_luminance,
        algorithm=algorithm,
        illumination_mode=args.illumination,
    )
    save_image(result, args.output, source_info=source_info)
    print(f"已保存: {Path(args.output).resolve()}")
    print(f"估算平均色差 ΔE: {report.estimated_mean_delta_e:.1f}")
    if report.quality:
        quality = report.quality
        print(f"输入可信度: {quality.score}/100（{quality.summary}）")
        for warning in quality.warnings:
            print(f"提示: {warning}")
    return 0


class ColorCalibratorApp:
    PREVIEW_SIZE = (760, 480)

    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title("独立样品校色工具")
        self.root.minsize(1080, 720)
        self.source_path: Path | None = None
        self.reference_path: Path | None = None
        self.source: Image.Image | None = None
        self.reference: Image.Image | None = None
        self.result: Image.Image | None = None
        self.report: MatchReport | None = None
        self.source_info: dict = {}
        self.rect = (0.0, 0.0, 1.0, 1.0)
        self.drag_start: tuple[float, float] | None = None
        self.display_box = (0, 0, 1, 1)
        self.source_photo = None
        self.reference_photo = None
        self.result_photo = None
        self.diagnostic_overlay: Image.Image | None = None
        self.transform_plan = None
        self.plan_signature = None

        self.mode = tk.StringVar(value="color")
        self.algorithm = tk.StringVar(value="classic")
        self.illumination = tk.StringVar(value="off")
        self.show_diagnostic = tk.BooleanVar(value=False)
        self.strength = tk.DoubleVar(value=85)
        self.status = tk.StringVar(value="先载入新拍大图和旧小样；如有背景，请在新图上框选纯样品区域。")
        self.selection_text = tk.StringVar(value="取色区域：整张新图")
        self.quality_text = tk.StringVar(value="质量报告：生成预览后显示")

        toolbar = ttk.Frame(root, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="1. 打开新拍大图", command=self.load_source).pack(side="left", padx=4)
        ttk.Button(toolbar, text="2. 打开旧小样", command=self.load_reference).pack(side="left", padx=4)
        ttk.Button(toolbar, text="重置为整图取色", command=self.reset_selection).pack(side="left", padx=12)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Radiobutton(
            toolbar, text="只匹配颜色（推荐）", variable=self.mode, value="color"
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            toolbar, text="颜色 + 明暗", variable=self.mode, value="full"
        ).pack(side="left", padx=4)
        ttk.Label(toolbar, text="强度").pack(side="left", padx=(16, 2))
        ttk.Scale(toolbar, from_=0, to=100, variable=self.strength, length=140).pack(side="left")
        ttk.Label(toolbar, textvariable=self.strength, width=4).pack(side="left")
        self.preview_button = ttk.Button(toolbar, text="3. 生成预览", command=self.generate_preview)
        self.preview_button.pack(side="right", padx=4)
        self.save_button = ttk.Button(toolbar, text="4. 保存全分辨率", command=self.save_full, state="disabled")
        self.save_button.pack(side="right", padx=4)

        advanced = ttk.Frame(root, padding=(14, 0, 14, 8))
        advanced.pack(fill="x")
        ttk.Label(advanced, text="算法").pack(side="left")
        algorithm_box = ttk.Combobox(
            advanced, textvariable=self.algorithm, state="readonly", width=18,
            values=("classic", "distribution"),
        )
        algorithm_box.pack(side="left", padx=(4, 16))
        algorithm_box.bind("<<ComboboxSelected>>", self._algorithm_changed)
        ttk.Label(advanced, text="classic 经典 / distribution 精细").pack(side="left")
        ttk.Label(advanced, text="空间光照").pack(side="left", padx=(22, 4))
        illumination_box = ttk.Combobox(
            advanced, textvariable=self.illumination, state="readonly", width=10,
            values=("off", "chroma", "full"),
        )
        illumination_box.pack(side="left")
        illumination_box.bind("<<ComboboxSelected>>", self._illumination_changed)
        ttk.Label(advanced, text="off 关闭 / chroma 色偏 / full 色偏+明暗").pack(
            side="left", padx=(5, 16)
        )
        ttk.Checkbutton(
            advanced, text="显示问题像素", variable=self.show_diagnostic,
            command=self.render_source,
        ).pack(side="right")

        content = ttk.Panedwindow(root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        left = ttk.Labelframe(content, text="新拍大图（拖鼠标框选纯样品取色区）", padding=6)
        right = ttk.Labelframe(content, text="校色预览", padding=6)
        content.add(left, weight=1)
        content.add(right, weight=1)
        self.source_canvas = tk.Canvas(left, background="#252525", highlightthickness=0)
        self.source_canvas.pack(fill="both", expand=True)
        self.source_canvas.bind("<Configure>", lambda _event: self.render_source())
        self.source_canvas.bind("<ButtonPress-1>", self.begin_selection)
        self.source_canvas.bind("<B1-Motion>", self.update_selection)
        self.source_canvas.bind("<ButtonRelease-1>", self.end_selection)
        ttk.Label(left, textvariable=self.selection_text).pack(anchor="w", pady=(5, 0))

        self.result_canvas = tk.Canvas(right, background="#252525", highlightthickness=0)
        self.result_canvas.pack(fill="both", expand=True)
        self.result_canvas.bind("<Configure>", lambda _event: self.render_result())

        footer = ttk.Frame(root, padding=(10, 4, 10, 10))
        footer.pack(fill="x")
        ref_box = ttk.Labelframe(footer, text="旧小样参考", padding=4)
        ref_box.pack(side="left")
        self.reference_label = ttk.Label(ref_box, text="尚未载入", width=24, anchor="center")
        self.reference_label.pack()
        messages = ttk.Frame(footer)
        messages.pack(side="left", fill="x", expand=True, padx=12)
        ttk.Label(messages, textvariable=self.status, anchor="w", wraplength=760).pack(
            fill="x"
        )
        ttk.Label(messages, textvariable=self.quality_text, anchor="w", wraplength=760).pack(
            fill="x", pady=(3, 0)
        )

    def _open_dialog(self, title):
        from tkinter import filedialog

        return filedialog.askopenfilename(title=title, filetypes=IMAGE_TYPES)

    def _algorithm_changed(self, _event=None):
        if self.algorithm.get() == "classic":
            self.illumination.set("off")

    def _illumination_changed(self, _event=None):
        if self.illumination.get() != "off":
            self.algorithm.set("distribution")

    def load_source(self):
        path = self._open_dialog("选择新拍的大面积样品图")
        if not path:
            return
        try:
            with Image.open(path) as opened:
                self.source_info = dict(opened.info)
            self.source = open_image(path)
            self.source_path = Path(path)
            self.result = None
            self.report = None
            self.diagnostic_overlay = None
            self.transform_plan = None
            self.plan_signature = None
            self.quality_text.set("质量报告：生成预览后显示")
            self.reset_selection()
            self.render_source()
            self.render_result()
            self.save_button.configure(state="disabled")
            self.status.set(f"新图：{self.source_path.name}（{self.source.width}×{self.source.height}）")
        except Exception as exc:
            self.show_error(exc)

    def load_reference(self):
        path = self._open_dialog("选择旧的小面积参考图")
        if not path:
            return
        try:
            self.reference = open_image(path)
            self.reference_path = Path(path)
            self.transform_plan = None
            self.plan_signature = None
            preview = self.reference.copy()
            preview.thumbnail((230, 130), Image.Resampling.LANCZOS)
            self.reference_photo = ImageTk.PhotoImage(preview)
            self.reference_label.configure(image=self.reference_photo, text="")
            self.status.set(f"参考：{self.reference_path.name}（{self.reference.width}×{self.reference.height}）")
        except Exception as exc:
            self.show_error(exc)

    def _fit(self, image: Image.Image, canvas) -> tuple[Image.Image, tuple[int, int, int, int]]:
        width = max(20, canvas.winfo_width())
        height = max(20, canvas.winfo_height())
        preview = image.copy()
        preview.thumbnail((width, height), Image.Resampling.LANCZOS)
        x = (width - preview.width) // 2
        y = (height - preview.height) // 2
        return preview, (x, y, preview.width, preview.height)

    def render_source(self):
        self.source_canvas.delete("all")
        if self.source is None:
            self.source_canvas.create_text(20, 20, anchor="nw", fill="white", text="请打开新拍大图")
            return
        preview, self.display_box = self._fit(self.source, self.source_canvas)
        if self.show_diagnostic.get() and self.diagnostic_overlay is not None:
            overlay = self.diagnostic_overlay.resize(preview.size, Image.Resampling.NEAREST)
            preview = Image.alpha_composite(preview.convert("RGBA"), overlay.convert("RGBA"))
        self.source_photo = ImageTk.PhotoImage(preview)
        x, y, width, height = self.display_box
        self.source_canvas.create_image(x, y, image=self.source_photo, anchor="nw")
        rx, ry, rw, rh = self.rect
        self.source_canvas.create_rectangle(
            x + rx * width, y + ry * height,
            x + (rx + rw) * width, y + (ry + rh) * height,
            outline="#00e5ff", width=3, tags="selection",
        )

    def render_result(self):
        self.result_canvas.delete("all")
        if self.result is None:
            self.result_canvas.create_text(20, 20, anchor="nw", fill="white", text="生成后在此预览")
            return
        preview, box = self._fit(self.result, self.result_canvas)
        self.result_photo = ImageTk.PhotoImage(preview)
        self.result_canvas.create_image(box[0], box[1], image=self.result_photo, anchor="nw")

    def _canvas_point(self, event) -> tuple[float, float]:
        x, y, width, height = self.display_box
        px = min(max((event.x - x) / max(width, 1), 0.0), 1.0)
        py = min(max((event.y - y) / max(height, 1), 0.0), 1.0)
        return px, py

    def begin_selection(self, event):
        if self.source is not None:
            self.drag_start = self._canvas_point(event)

    def update_selection(self, event):
        if self.drag_start is None:
            return
        current = self._canvas_point(event)
        x0, x1 = sorted((self.drag_start[0], current[0]))
        y0, y1 = sorted((self.drag_start[1], current[1]))
        if x1 - x0 >= 0.01 and y1 - y0 >= 0.01:
            self.rect = (x0, y0, x1 - x0, y1 - y0)
            self.render_source()

    def end_selection(self, event):
        self.update_selection(event)
        self.drag_start = None
        x, y, width, height = self.rect
        self.selection_text.set(
            f"取色区域：x={x:.3f}, y={y:.3f}, 宽={width:.3f}, 高={height:.3f}"
        )

    def reset_selection(self):
        self.rect = (0.0, 0.0, 1.0, 1.0)
        self.selection_text.set("取色区域：整张新图")
        self.render_source()

    def _preview_inputs(self):
        if self.source is None or self.reference is None:
            raise ValueError("请先载入新拍大图和旧小样")
        source = self.source.copy()
        source.thumbnail(self.PREVIEW_SIZE, Image.Resampling.LANCZOS)
        reference = self.reference.copy()
        return source, reference

    def _set_busy(self, message):
        self.status.set(message)
        self.preview_button.configure(state="disabled")
        self.save_button.configure(state="disabled")

    def _finish_busy(self):
        self.preview_button.configure(state="normal")
        if self.result is not None:
            self.save_button.configure(state="normal")

    def generate_preview(self):
        try:
            source, reference = self._preview_inputs()
        except Exception as exc:
            self.show_error(exc)
            return
        self._set_busy("正在生成预览……")
        rect = self.rect
        strength = self.strength.get() / 100.0
        preserve_luminance = self.mode.get() == "color"
        algorithm = self.algorithm.get()
        illumination = self.illumination.get()
        signature = (
            self.source_path, self.reference_path, rect, preserve_luminance,
            algorithm, illumination,
        )

        def work():
            try:
                plan = None
                if algorithm == "distribution":
                    plan, _ = build_sample_match_plan(
                        self.source, reference, source_rect=rect,
                        preserve_luminance=preserve_luminance,
                        algorithm=algorithm, illumination_mode=illumination,
                    )
                result, report = match_sample_color(
                    source, reference, source_rect=rect,
                    strength=strength,
                    preserve_luminance=preserve_luminance,
                    algorithm=algorithm,
                    illumination_mode=illumination,
                    transform_plan=plan,
                )
                self.root.after(
                    0, lambda: self._preview_done(result, report, plan, signature)
                )
            except Exception as exc:
                self.root.after(0, lambda error=exc: self.show_error(error))

        threading.Thread(target=work, daemon=True).start()

    def _preview_done(self, result, report, plan=None, signature=None):
        self.result = result
        self.report = report
        self.transform_plan = plan
        self.plan_signature = signature
        self.diagnostic_overlay = report.quality.diagnostic_overlay if report.quality else None
        self.render_source()
        self.render_result()
        self._finish_busy()
        self.status.set(
            f"预览完成。估算平均色差 ΔE={report.estimated_mean_delta_e:.1f}；"
            "满意后点“保存全分辨率”。"
        )
        if report.quality:
            quality = report.quality
            warnings = "；".join(quality.warnings) if quality.warnings else "未发现明显风险"
            self.quality_text.set(
                f"质量报告：{quality.score}/100 · {quality.summary} · "
                f"可用像素 {quality.source_usable_ratio:.0%} · {warnings}"
            )

    def save_full(self):
        if self.source is None or self.reference is None or self.source_path is None:
            return
        from tkinter import filedialog

        suggested = self.source_path.stem + "_校色" + self.source_path.suffix
        path = filedialog.asksaveasfilename(
            title="保存全分辨率校色图", initialfile=suggested,
            defaultextension=self.source_path.suffix,
            filetypes=IMAGE_TYPES,
        )
        if not path:
            return
        self._set_busy("正在处理并保存全分辨率图片……")
        source = self.source.copy()
        reference = self.reference.copy()
        rect = self.rect
        strength = self.strength.get() / 100.0
        preserve_luminance = self.mode.get() == "color"
        algorithm = self.algorithm.get()
        illumination = self.illumination.get()
        signature = (
            self.source_path, self.reference_path, rect, preserve_luminance,
            algorithm, illumination,
        )
        plan = self.transform_plan if self.plan_signature == signature else None

        def work():
            try:
                result, report = match_sample_color(
                    source, reference, source_rect=rect, strength=strength,
                    preserve_luminance=preserve_luminance,
                    algorithm=algorithm,
                    illumination_mode=illumination,
                    transform_plan=plan,
                )
                save_image(result, path, source_info=self.source_info)
                self.root.after(0, lambda: self._save_done(path, report))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self.show_error(error))

        threading.Thread(target=work, daemon=True).start()

    def _save_done(self, path, report):
        self._finish_busy()
        self.status.set(f"已保存：{path}（估算平均色差 ΔE={report.estimated_mean_delta_e:.1f}）")

    def show_error(self, exc):
        from tkinter import messagebox

        self._finish_busy()
        self.status.set(f"处理失败：{exc}")
        messagebox.showerror("校色工具", str(exc))


def run_gui() -> int:
    import tkinter as tk

    root = tk.Tk()
    ColorCalibratorApp(root)
    root.mainloop()
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if any((args.source, args.reference, args.output)):
        return run_cli(args)
    return run_gui()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        traceback.print_exc()
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
