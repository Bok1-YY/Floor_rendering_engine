"""Single-worker background lifecycle; cancelling a waiter does not kill its thread."""
import asyncio
import threading
from collections import Counter


class BackgroundTasks:
    def __init__(self, logger):
        self.logger = logger
        self.tasks = set()
        self.metadata = {}
        self.stopping = threading.Event()
        self._reservations = Counter()

    def reserve(self, kind, limit):
        if self.stopping.is_set() or self._reservations[kind] >= limit:
            return False
        self._reservations[kind] += 1
        return True

    def release(self, kind):
        self._reservations[kind] = max(0, self._reservations[kind] - 1)

    def spawn(self, coro, *, kind='job', reference=''):
        if self.stopping.is_set():
            coro.close()
            raise RuntimeError('服务正在退出，不接受新任务')
        task = asyncio.create_task(coro, name=f'{kind}:{reference}')
        self.tasks.add(task)
        self.metadata[task] = (kind, reference)
        task.add_done_callback(self._done)
        return task

    def _done(self, task):
        context = self.metadata.pop(task, ('unknown', ''))
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            self.logger.error('后台任务失败 kind=%s reference=%s', *context,
                              exc_info=(type(error), error, error.__traceback__))

    async def shutdown(self, timeout=15):
        self.stopping.set()
        tasks = tuple(self.tasks)
        if not tasks:
            return 0
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        # Keep strong references. A to_thread operation may still save a paid
        # result; do not pretend task.cancel() terminates the underlying call.
        if pending:
            self.logger.warning('退出等待期限已到，%s 个任务仍在收尾', len(pending))
        return len(pending)
