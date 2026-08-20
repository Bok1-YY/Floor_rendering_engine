import type {
  WholeHomeHumanReviewStatus,
  WholeHomeReviewState,
  WholeHomeReviewableArtifact,
} from "./types";

export const WHOLE_HOME_REJECT_TAGS = Object.freeze([
  "户型墙线错误",
  "门窗/开口错误",
  "固定物错误",
  "房间身份错误",
  "机位构图差",
  "地板颜色纹理错误",
  "材质错误",
  "明显幻觉",
  "其他",
]);

export function reviewValidationMessage(
  status: WholeHomeHumanReviewStatus,
  tags: readonly string[],
): string {
  if (status === "reject" && tags.filter(Boolean).length === 0) {
    return "拒绝图片时至少选择一个失败原因";
  }
  return "";
}

export function canCompleteHumanReview(state: WholeHomeReviewState | null | undefined): boolean {
  if (!state?.can_complete || state.pending_count !== 0) return false;
  return state.round_status === "awaiting_human_review" || state.round_status === "review_not_required";
}

export function canContinueHumanReview(state: WholeHomeReviewState | null | undefined): boolean {
  return Boolean(
    state?.round_status === "review_complete"
    && state.completion_event_id
    && state.pending_count === 0,
  );
}

export function isWholeHomeGenerationLocked(state: WholeHomeReviewState | null | undefined): boolean {
  return state?.round_status === "awaiting_human_review" || state?.round_status === "review_not_required";
}

export interface WholeHomeArtifactGroup {
  room_id: string;
  artifacts: WholeHomeReviewableArtifact[];
  counts: Record<WholeHomeHumanReviewStatus, number>;
}

export function groupWholeHomeReviewables(
  artifacts: readonly WholeHomeReviewableArtifact[],
): WholeHomeArtifactGroup[] {
  const groups = new Map<string, WholeHomeReviewableArtifact[]>();
  for (const artifact of artifacts) {
    const roomId = artifact.room_id || "unknown-room";
    groups.set(roomId, [...(groups.get(roomId) || []), artifact]);
  }
  return [...groups.entries()].map(([room_id, rows]) => ({
    room_id,
    artifacts: rows,
    counts: {
      pass: rows.filter((row) => row.review_status === "pass").length,
      backup: rows.filter((row) => row.review_status === "backup").length,
      reject: rows.filter((row) => row.review_status === "reject").length,
      unreviewed: rows.filter((row) => row.review_status === "unreviewed").length,
    },
  }));
}
