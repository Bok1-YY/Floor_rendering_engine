export interface StorageAuditView {
  snapshot_id: string;
  scopes: Array<"samples" | "thumbnails">;
  samples: {
    files: number;
    unique_contents: number;
    duplicate_files: number;
    bytes: number;
    duplicate_bytes: number;
    legacy_inline_records: number;
    missing_references: number;
    examples: Array<{ hash: string; copies: number; example: string }>;
  };
  thumbnails: { files: number; unique_contents: number; duplicate_files: number; bytes: number };
  orphan_results: { files: number; bytes: number; examples: string[]; paths: string[]; report_only: true };
  records: { files: number; entries: number };
  data_root_label: string;
}

export interface StorageCleanupView {
  ok: boolean;
  snapshot_id: string;
  rewritten_records: number;
  removed_sample_files: number;
  sample_files_reduced: number;
  removed_thumbnail_files: number;
  freed_bytes: number;
  backup_manifest: string;
  audit: StorageAuditView;
}

export interface AssetDeleteView {
  ok: boolean;
  file_deleted?: boolean;
  files_deleted?: number;
  kept_shared: boolean | number;
  thumbnail_files_deleted: number;
  freed_bytes: number;
  job_cards_updated: number;
  cleanup_error?: string;
  cleanup_errors?: string[];
}

export interface QuarantineEntryView {
  entry_id: string;
  original_relpath: string;
  sha256: string;
  size: number;
  reason: string;
  quarantined_at: string;
  quarantined_at_epoch: number;
  purge_eligible_at: string;
  purge_eligible_at_epoch: number;
  purge_eligible: boolean;
  payload_name: string;
  status: "quarantined" | "restored" | "purged";
  restored_at?: string;
  purged_at?: string;
  freed_bytes?: number;
}
