"use client";

import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { QuarantineEntryView, StorageAuditView } from "@/lib/types";

const formatBytes = (value: number) => value < 1024 * 1024
  ? `${(value / 1024).toFixed(1)} KB`
  : `${(value / 1024 / 1024).toFixed(2)} MB`;

export function useStorageMaintenance() {
  const [storageAudit, setStorageAudit] = useState<StorageAuditView | null>(null);
  const [storageScanning, setStorageScanning] = useState(false);
  const [storageCleaning, setStorageCleaning] = useState(false);
  const [quarantineEntries, setQuarantineEntries] = useState<QuarantineEntryView[]>([]);
  const [quarantineBusy, setQuarantineBusy] = useState("");

  async function scanStorage() {
    setStorageScanning(true);
    try {
      const [audit, quarantine] = await Promise.all([api.storageAudit(), api.listQuarantine()]);
      setStorageAudit(audit);
      setQuarantineEntries(quarantine);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setStorageScanning(false);
    }
  }

  async function cleanStorage() {
    if (!storageAudit) return;
    const estimated = storageAudit.samples.duplicate_bytes + storageAudit.thumbnails.bytes;
    if (!window.confirm(
      `将先备份记录并合并 ${storageAudit.samples.duplicate_files} 个重复小样，同时清空 ` +
      `${storageAudit.thumbnails.files} 个可再生缩略图，预计释放 ${formatBytes(estimated)}。继续吗？`,
    )) return;
    setStorageCleaning(true);
    try {
      const result = await api.cleanupStorage(storageAudit.snapshot_id);
      setStorageAudit(result.audit);
      toast.success(`清理完成，释放 ${formatBytes(result.freed_bytes)}；备份清单：${result.backup_manifest}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setStorageCleaning(false);
    }
  }

  async function quarantineOrphansNow() {
    const paths = storageAudit?.orphan_results.paths || [];
    if (!storageAudit || paths.length === 0) return;
    if (!window.confirm(`把 ${paths.length} 个当前无引用文件移动到30天可恢复隔离区？这不会立即释放磁盘空间。`)) return;
    setQuarantineBusy("quarantine");
    try {
      const result = await api.quarantineOrphans(storageAudit.snapshot_id, paths);
      setStorageAudit(result.audit);
      setQuarantineEntries(await api.listQuarantine());
      toast.success(`已隔离 ${result.entries.length} 个文件；30天内可恢复`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setQuarantineBusy("");
    }
  }

  async function restoreQuarantineEntry(entry: QuarantineEntryView) {
    setQuarantineBusy(entry.entry_id);
    try {
      await api.restoreQuarantine(entry.entry_id);
      await scanStorage();
      toast.success("文件已恢复到原路径");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setQuarantineBusy("");
    }
  }

  async function purgeQuarantineEntry(entry: QuarantineEntryView) {
    const phrase = window.prompt(`永久删除 ${entry.original_relpath}？请输入“永久删除”确认。`);
    if (phrase !== "永久删除") return;
    setQuarantineBusy(entry.entry_id);
    try {
      const result = await api.purgeQuarantine(entry.entry_id, phrase);
      setQuarantineEntries(await api.listQuarantine());
      toast.success(`已永久删除，释放 ${formatBytes(result.freed_bytes)}`);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setQuarantineBusy("");
    }
  }

  return {
    cleanStorage,
    purgeQuarantineEntry,
    quarantineBusy,
    quarantineEntries,
    quarantineOrphansNow,
    restoreQuarantineEntry,
    scanStorage,
    storageAudit,
    storageCleaning,
    storageScanning,
  };
}
