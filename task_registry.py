"""进程内任务注册表 —— jobs / previews / inpaints 三套编排状态的统一容器。

历史上 server_api 为任务队列、快速预览、生成式修补各复制了一份
「dict + threading.Lock + 取消集合 + 容量上限 + trim 函数」。本类把这四件套收敛为一个
泛型容器,行为逐字对齐原三套实现:

- **成员管理**加锁(add/get/pop/snapshot/replace/update_fields/view/trim/persist);
  **条目内容的字段级修改不加锁**(JobRecord 属性赋值、entry dict 原地改),与原实现一致。
- **取消集合**的 add/discard/判断不加锁(GIL 下 set 原子操作),与原实现一致。
- **trim 只删最旧的终态条目**,in-flight 永不删;内部按插入序存储(旧→新),
  等价于原 jobs 的「从列表尾向前扫」与 previews/inpaints 的「按 ts 升序删」
  (entry 的 ts 在创建时一次性赋值,插入序 == ts 序)。
- **单 worker 假设**:与 serve.py 的打包约束一致,本容器只做线程安全,不做跨进程共享。

复合操作(检查-转移状态、背压检查+插入等必须在一把锁内完成的逻辑)走 locked()
逃生口;在 locked() 块内只允许直接操作 entries 与调用 trim_locked() / 取消集合方法,
禁止再调 add/get/pop/snapshot/persist 等加锁方法(threading.Lock 不可重入,会死锁)。
"""
import threading
from contextlib import contextmanager
from typing import Callable, Dict, Iterable, List, Optional, Tuple


class TaskRegistry:
    """tid → entry 的有序注册表。entry 类型不限(JobRecord 或 dict)。

    Args:
        name: 标识名,仅用于日志/调试。
        max_entries: 常驻上限,trim 时收口到此值(减去 reserve)。
        is_terminal: entry → bool,终态判定;只有终态条目会被 trim 删除。
        on_evict: trim 删除条目时的回调(如删临时候选文件);None = 无动作。
        on_persist: persist() 时收到 snapshot() 结果的回调;None = persist 为空操作。
        newest_first: True 时 snapshot()/replace() 的公开顺序为「最新在前」
            (原 _job_history 的 insert(0) 语义);False 为插入序(旧→新)。
    """

    def __init__(self, name: str, *, max_entries: int,
                 is_terminal: Callable, on_evict: Optional[Callable] = None,
                 on_persist: Optional[Callable] = None, newest_first: bool = False):
        self.name = name
        self._max = max_entries
        self._is_terminal = is_terminal
        self._on_evict = on_evict
        self._on_persist = on_persist
        self._newest_first = newest_first
        self._lock = threading.Lock()
        self._entries: Dict = {}      # 插入序 = 旧→新
        self._cancelled: set = set()
        self._generation = 0          # 全局取消代次(cancel-all 用,见 is_cancelled)

    # ── 成员管理(加锁) ──────────────────────────────────────────

    def add(self, tid: str, entry, *, reserve: int = 0) -> None:
        """登记新条目并顺手收口(原「insert + _trim_xxx()」的合并)。"""
        with self._lock:
            self._entries[tid] = entry
            self._trim_locked(reserve=reserve)

    def get(self, tid: str):
        """返回条目本体(可变引用),不存在返回 None。"""
        with self._lock:
            return self._entries.get(tid)

    def pop(self, tid: str):
        """移除并返回条目(同时清掉其取消标记),不存在返回 None。"""
        with self._lock:
            entry = self._entries.pop(tid, None)
        self._cancelled.discard(tid)
        return entry

    def snapshot(self) -> List:
        """有序浅拷贝:newest_first 时最新在前(等价原 list(_job_history))。"""
        with self._lock:
            items = list(self._entries.values())
        return items[::-1] if self._newest_first else items

    def replace(self, pairs: Iterable[Tuple[str, object]]) -> None:
        """整体替换(启动恢复用)。pairs 按公开顺序给出(newest_first 时最新在前)。"""
        items = list(pairs)
        if self._newest_first:
            items.reverse()
        with self._lock:
            self._entries = dict(items)

    def update_fields(self, tid: str, **fields) -> bool:
        """dict 型条目的原子字段更新;条目不存在返回 False(与原「if pid in ...」一致)。"""
        with self._lock:
            entry = self._entries.get(tid)
            if entry is None:
                return False
            entry.update(fields)
            return True

    def view(self, tid: str) -> Optional[dict]:
        """dict 型条目的浅拷贝快照(等价原「dict(_previews.get(pid) or {})」),不存在返回 None。"""
        with self._lock:
            entry = self._entries.get(tid)
            return dict(entry) if entry is not None else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── 取消(不加锁,镜像原裸 set 操作) ────────────────────────────

    def request_cancel(self, tid: str) -> None:
        self._cancelled.add(tid)

    def clear_cancelled(self, tid: str) -> None:
        self._cancelled.discard(tid)

    def is_cancelled(self, tid: str, generation: Optional[int] = None) -> bool:
        """本条目被单独停 → True;或全局代次已超过任务捕获的旧值 → True。"""
        return tid in self._cancelled or (generation is not None and generation < self._generation)

    @property
    def generation(self) -> int:
        return self._generation

    def bump_generation(self) -> None:
        """cancel-all:in-flight 任务捕获的旧代次 < 新值即自行退出。"""
        self._generation += 1

    # ── 生命周期 ─────────────────────────────────────────────

    def trim(self, *, reserve: int = 0) -> None:
        with self._lock:
            self._trim_locked(reserve=reserve)

    def trim_locked(self, *, reserve: int = 0) -> None:
        """供 locked() 块内调用的免锁 trim;其余场景用 trim()/add()。"""
        self._trim_locked(reserve=reserve)

    def _trim_locked(self, *, reserve: int = 0) -> None:
        """收口到 max_entries - reserve:按旧→新只删终态条目,in-flight 永不删。
        终态不足以降到限额时容忍超限(与原实现一致)。调用方须已持锁。"""
        limit = max(0, self._max - max(0, reserve))
        over = len(self._entries) - limit
        if over <= 0:
            return
        victims = [tid for tid, e in self._entries.items() if self._is_terminal(e)][:over]
        for tid in victims:
            entry = self._entries.pop(tid)
            if self._on_evict is not None:
                self._on_evict(entry)
            self._cancelled.discard(tid)

    def persist(self) -> None:
        """快照后在锁外调 on_persist(原 _persist_jobs 语义);未配置则空操作。
        禁止在 locked() 块内调用(会死锁),与原「_persist_jobs 必须在锁外」同一约束。"""
        if self._on_persist is None:
            return
        self._on_persist(self.snapshot())

    @contextmanager
    def locked(self):
        """复合操作逃生口:持锁yield内部 entries(插入序 旧→新)。
        块内只许直接操作 entries、调 trim_locked() 与取消集合方法。"""
        with self._lock:
            yield self._entries
