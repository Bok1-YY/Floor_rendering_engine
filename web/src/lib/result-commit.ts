import { api, ApiError } from '@/lib/api';
import type { JobView } from '@/lib/types';
import { toast } from 'sonner';

export type ResultCommitView = { ok: boolean; commit_id: string; result_url: string; job?: JobView };

/** A local retry only; this helper never invokes a generation endpoint. */
export function reportCommitError(error: unknown, onRecovered?: (result: ResultCommitView) => void) {
  if (error instanceof ApiError && error.code === 'result_commit_pending'
      && error.detail && typeof error.detail === 'object' && 'commit_id' in error.detail
      && (!('image_saved' in error.detail) || error.detail.image_saved !== false)) {
    const id = String(error.detail.commit_id);
    let recovering = false;
    toast.error(error.message, {
      id: `commit:${id}`, duration: Infinity,
      action: { label: '恢复本地写入', onClick: async () => {
        if (recovering) return;
        recovering = true;
        try {
          const result = await api.retryResultCommit(id);
          toast.dismiss(`commit:${id}`);
          toast.success('本地结果已恢复写入，未再次调用模型');
          onRecovered?.(result);
          window.dispatchEvent(new Event('floor-result-restored'));
        } catch (nextError) {
          reportCommitError(nextError, onRecovered);
        } finally { recovering = false; }
      } },
    });
  } else toast.error(error instanceof Error ? error.message : String(error));
}
