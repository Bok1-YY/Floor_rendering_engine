// server_api.py 的 typed 客户端。base 走 NEXT_PUBLIC_API_BASE（.env.local）。
import type {
  JobView,
  JobSubmit,
  EditRequest,
  Swatch,
  ConfigView,
  ConfigPatch,
  ModelsView,
  OptionsView,
  Recipe,
  RecordEntry,
  RecordFile,
  ResultReviewPatch,
  FailureKB,
  UsageSummary,
  FloorAnalyze,
  RecordEditRequest,
  PreviewRequest,
  PreviewView,
  OmakaseScenesResponse,
  CustomRecipe,
  GenParams,
  ReviewSummary,
  ReviewGalleryItem,
  ColorMatchPreviewRequest,
  ColorMatchPreviewView,
  JobColorMatchRequest,
  RecordColorMatchRequest,
} from "./types";

// dev（next dev 在 :3000）用 .env.local 里的 NEXT_PUBLIC_API_BASE 指到后端 :7870；
// 生产静态导出由后端同源托管，走空 base → 相对地址（window.location.origin），
// 这样无论客户从 localhost 还是局域网 IP 打开都能命中后端。
export const API =
  process.env.NEXT_PUBLIC_API_BASE ||
  (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:7870");

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      detail = (j && (j.detail || JSON.stringify(j))) || detail;
    } catch {
      /* 非 JSON 错误体，保留状态码 */
    }
    throw new Error(detail);
  }
  return (await r.json()) as T;
}

const jget = <T>(p: string) => fetch(API + p).then((r) => handle<T>(r));

const jsend = <T>(p: string, method: "POST" | "PUT", body?: unknown, signal?: AbortSignal) =>
  fetch(API + p, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  }).then((r) => handle<T>(r));

async function upload(path: string, file: File): Promise<Swatch> {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(API + path, { method: "POST", body: fd }).then((r) =>
    handle<Swatch>(r),
  );
}

export const api = {
  /** 把后端给的相对 URL(/outputs.. /thumb..)拼成可用的绝对地址 */
  imgUrl: (p: string) => (!p ? "" : p.startsWith("http") ? p : API + p),
  health: () => jget<{ ok: boolean }>("/api/healthz"),

  uploadFloor: (f: File) => upload("/api/uploads/floor", f),
  uploadRoom: (f: File) => upload("/api/uploads/room", f),
  uploadRef: (f: File) => upload("/api/uploads/ref", f),

  createJob: (req: JobSubmit) => jsend<JobView>("/api/jobs", "POST", req),
  listJobs: (limit = 50) => jget<JobView[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => jget<JobView>(`/api/jobs/${id}`),
  cancelJob: (id: string) =>
    jsend<{ cancelled: boolean }>(`/api/jobs/${id}/cancel`, "POST"),
  cancelAll: () => jsend<{ stopped: number }>(`/api/jobs/cancel-all`, "POST"),
  clearCompleted: () =>
    jsend<{ cleared: number }>(`/api/jobs/clear-completed`, "POST"),
  deleteJob: (id: string) =>
    jsend<{ deleted: number }>(`/api/jobs/${id}/delete`, "POST"),
  retryJob: (id: string) => jsend<JobView>(`/api/jobs/${id}/retry`, "POST"),
  jobResult: (id: string, model: "b2" | "pro", idx: number) =>
    jget<{ model: string; idx: number; total: number; url: string; thumb: string }>(
      `/api/jobs/${id}/result?model=${model}&idx=${idx}`,
    ),
  polishJob: (id: string) => jsend<JobView>(`/api/jobs/${id}/polish`, "POST"),
  editJob: (id: string, body: EditRequest) =>
    jsend<JobView>(`/api/jobs/${id}/edit`, "POST", body),

  // ── 快速预览（Nano Banana 2 Lite · 1K，与 4K 队列解耦；短轮询状态）──
  createPreview: (req: PreviewRequest) =>
    jsend<{ preview_id: string; status: string }>("/api/preview", "POST", req),
  previewStatus: (pid: string) => jget<PreviewView>(`/api/preview/${pid}`),
  cancelPreview: (pid: string) =>
    jsend<{ cancelled: boolean }>(`/api/preview/${pid}/cancel`, "POST"),

  recentSwatches: (limit = 24) =>
    jget<Swatch[]>(`/api/swatches/recent?limit=${limit}`),

  listRecords: () => jget<RecordFile[]>(`/api/records`),
  loadRecord: (jsonPath: string) =>
    jget<RecordEntry[]>(
      `/api/records/load?json_path=${encodeURIComponent(jsonPath)}`,
    ),
  reveal: (json_path: string, record_id: string, password: string) =>
    jsend<{ text: string; ok: boolean }>(`/api/records/reveal`, "POST", {
      json_path,
      record_id,
      password,
    }),

  recipes: (tone = "", limit = 6) =>
    jget<Recipe[]>(
      `/api/recipes?tone=${encodeURIComponent(tone)}&limit=${limit}`,
    ),

  // ── 自定义配方（我的配方）──
  listCustomRecipes: () => jget<CustomRecipe[]>(`/api/recipes/custom`),
  addCustomRecipe: (name: string, params: GenParams) =>
    jsend<CustomRecipe>(`/api/recipes/custom`, "POST", { name, params }),
  updateCustomRecipe: (rid: string, patch: { name?: string; params?: GenParams }) =>
    jsend<CustomRecipe>(`/api/recipes/custom/${rid}/update`, "POST", patch),
  deleteCustomRecipe: (rid: string) =>
    jsend<{ ok: boolean }>(`/api/recipes/custom/${rid}/delete`, "POST"),
  classifyFailure: (err: string) =>
    jsend<FailureKB>(`/api/failure/classify`, "POST", { err }),
  connectionTest: () => jget<{ result: string }>(`/api/connection/test`),

  // ── Omakase：Gemini 主线路生成场景，DeepSeek 配置后自动备用 ──
  omakaseScenes: (idea: string) =>
    jsend<OmakaseScenesResponse>(`/api/omakase/scenes`, "POST", { idea }),

  getConfig: () => jget<ConfigView>(`/api/config`),
  putConfig: (patch: ConfigPatch) =>
    jsend<ConfigView>(`/api/config`, "PUT", patch),
  getModels: () => jget<ModelsView>(`/api/models`),
  getOptions: () => jget<OptionsView>(`/api/options`),

  // ── STEP 2.5 迁移补齐 ──
  regenJob: (id: string, n: number) =>
    jsend<JobView>(`/api/jobs/${id}/regen?n=${n}`, "POST"),
  recordEdit: (body: RecordEditRequest) =>
    jsend<JobView>(`/api/records/edit`, "POST", body),
  deleteResult: (json_path: string, record_id: string, result_id: string) =>
    jsend<{ ok: boolean }>(`/api/records/result/delete`, "POST", {
      json_path,
      record_id,
      result_id,
    }),
  favoriteResult: (json_path: string, record_id: string, result_id: string) =>
    jsend<{ favorite: boolean }>(`/api/records/result/favorite`, "POST", {
      json_path,
      record_id,
      result_id,
    }),
  reviewResult: (body: ResultReviewPatch) =>
    jsend<{
      review_status: string;
      review_tags: string[];
      review_note: string;
      best: boolean;
      reviewed_at: string;
    }>(`/api/records/result/review`, "POST", body),
  deleteRecord: (json_path: string, record_id: string) =>
    jsend<{ ok: boolean }>(`/api/records/delete`, "POST", {
      json_path,
      record_id,
    }),
  exportHtmlUrl: (json_path: string) =>
    `${API}/api/records/export/html?json_path=${encodeURIComponent(json_path)}`,
  exportPptxUrl: (json_path: string) =>
    `${API}/api/records/export/pptx?json_path=${encodeURIComponent(json_path)}`,
  exportFavoritesUrl: () => `${API}/api/records/export/favorites-pptx`,
  floorAnalyze: (path: string) =>
    jget<FloorAnalyze>(`/api/floor/analyze?path=${encodeURIComponent(path)}`),
  getUsage: () => jget<UsageSummary>(`/api/usage`),
  getFailureRules: () => jget<FailureKB[]>(`/api/failure/rules`),

  // ── 评审复盘 ──
  getReviewSummary: () => jget<ReviewSummary>(`/api/review/summary`),
  getReviewGallery: (filter: "pass" | "best" = "pass", limit = 60) =>
    jget<ReviewGalleryItem[]>(`/api/review/gallery?filter=${filter}&limit=${limit}`),

  // ── PPTX 品牌 logo ──
  uploadLogo: (f: File) => upload("/api/uploads/logo", f),
  clearLogo: () => jsend<{ ok: boolean }>("/api/uploads/logo/clear", "POST"),

  // ── 手动校色（区域化 Reinhard）──
  colorMatchPreview: (b: ColorMatchPreviewRequest, signal?: AbortSignal) =>
    jsend<ColorMatchPreviewView>(`/api/color-match/preview`, "POST", b, signal),
  jobColorMatch: (id: string, b: JobColorMatchRequest) =>
    jsend<JobView>(`/api/jobs/${id}/color-match`, "POST", b),
  recordColorMatch: (b: RecordColorMatchRequest) =>
    jsend<{ ok: boolean; result_url: string }>(`/api/records/color-match`, "POST", b),
};
