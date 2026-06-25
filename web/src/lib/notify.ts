"use client";
// 任务完成提醒：浏览器系统通知 + Web Audio 提示音（移植自老版 _notify_job_end）。

export function requestNotifyPermission() {
  try {
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "default"
    ) {
      Notification.requestPermission().catch(() => {});
    }
  } catch {
    /* 不支持则忽略 */
  }
}

function beep(ok: boolean) {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    const c = new Ctx();
    const o = c.createOscillator();
    const g = c.createGain();
    o.connect(g);
    g.connect(c.destination);
    o.type = "sine";
    o.frequency.value = ok ? 880 : 300;
    g.gain.setValueAtTime(0.0001, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.3, c.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + 0.45);
    o.start();
    o.stop(c.currentTime + 0.45);
  } catch {
    /* 忽略 */
  }
}

export function notifyJobEnd(
  status: string,
  displayName: string,
  error?: string,
) {
  if (error && error.includes("取消")) return; // 用户主动取消不打扰
  const label =
    { done: "✅ 生成成功", partial: "⚠️ 部分完成", failed: "❌ 生成失败" }[
      status
    ] || status;
  const ok = status === "done" || status === "partial";
  try {
    if (
      typeof Notification !== "undefined" &&
      Notification.permission === "granted"
    ) {
      new Notification(label, {
        body: displayName + (error ? `  ·  ${error.slice(0, 80)}` : ""),
      });
    }
  } catch {
    /* 忽略 */
  }
  beep(ok);
}
