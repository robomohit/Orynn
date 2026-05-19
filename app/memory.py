from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .log_emitter import MAX_TEXT_FIELD_CHARS
from .models import MemoryItem

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Fallback collection (used when ChromaDB is not available or USE_CHROMA=0)
# ─────────────────────────────────────────────────────────────────────────


class _FallbackCollection:
    """Pure keyword-based fallback. No vector embeddings."""

    def __init__(self):
        self.docs: list = []

    def count(self):
        return len(self.docs)

    def add(self, documents, metadatas, ids):
        self.docs.extend(zip(ids, documents, metadatas))

    def query(self, query_texts, n_results, where=None):
        q_tokens = set(query_texts[0].lower().split())
        scored = []
        for _id, doc, meta in self.docs:
            if where and not all(meta.get(k) == v for k, v in where.items()):
                continue
            d_tokens = set(doc.lower().split())
            score = len(q_tokens & d_tokens)
            # Lower distance = better, mirroring Chroma cosine-distance semantics.
            distance = 1.0 - (score / max(len(q_tokens), 1))
            scored.append((distance, _id, doc, meta))
        ranked = sorted(scored, key=lambda x: x[0])[:n_results]
        return {
            "ids": [[r[1] for r in ranked]],
            "documents": [[r[2] for r in ranked]],
            "metadatas": [[r[3] for r in ranked]],
            "distances": [[r[0] for r in ranked]],
        }

    def get(self, limit, offset=0, where=None, **kwargs):
        if where:
            filtered = [(i, d, m) for i, d, m in self.docs if all(m.get(k) == v for k, v in where.items())]
        else:
            filtered = self.docs
        chunk = filtered[offset: offset + limit]
        return {
            "ids": [c[0] for c in chunk],
            "documents": [c[1] for c in chunk],
            "metadatas": [c[2] for c in chunk],
        }

    def delete(self, ids):
        id_set = set(ids)
        self.docs = [(i, d, m) for i, d, m in self.docs if i not in id_set]

    def update(self, ids, metadatas=None, documents=None):
        idx_by_id = {doc[0]: i for i, doc in enumerate(self.docs)}
        for k, _id in enumerate(ids):
            if _id not in idx_by_id:
                continue
            i = idx_by_id[_id]
            old_id, old_doc, old_meta = self.docs[i]
            new_doc = documents[k] if documents else old_doc
            new_meta = metadatas[k] if metadatas else old_meta
            self.docs[i] = (old_id, new_doc, new_meta)


# ─────────────────────────────────────────────────────────────────────────
# Short-term buffer with disk persistence (survives restarts)
# ─────────────────────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


class ShortTermBuffer:
    """Last-N-turns ring buffer per session, persisted to disk so the agent
    keeps its working memory across server restarts."""

    _MAX_TURNS = 20

    def __init__(self, persist_dir: Optional[Path] = None):
        self._sessions: Dict[str, deque] = {}
        self._persist_dir = persist_dir
        if persist_dir is not None:
            try:
                persist_dir.mkdir(parents=True, exist_ok=True)
                self._load()
            except Exception:
                pass

    def _load(self) -> None:
        if not self._persist_dir or not self._persist_dir.exists():
            return
        for f in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._sessions[f.stem] = deque(data, maxlen=self._MAX_TURNS)
            except Exception:
                continue

    def _save(self, session_id: str) -> None:
        if not self._persist_dir:
            return
        try:
            f = self._persist_dir / f"{session_id}.json"
            f.write_text(
                json.dumps(list(self._sessions[session_id]), ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            _log.warning("Failed to update memory recall_count metadata: %s", exc)

    def add(self, session_id: str, content: str) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = deque(maxlen=self._MAX_TURNS)
        self._sessions[session_id].append(content)
        self._save(session_id)

    def get(self, session_id: str) -> List[str]:
        return list(self._sessions.get(session_id, []))

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        if self._persist_dir:
            try:
                (self._persist_dir / f"{session_id}.json").unlink(missing_ok=True)
            except Exception:
                pass

    def known_sessions(self) -> List[str]:
        return list(self._sessions.keys())


# ─────────────────────────────────────────────────────────────────────────
# Hybrid scoring helpers (BM25, MMR, temporal decay, reinforcement)
# ─────────────────────────────────────────────────────────────────────────


def _bm25_scores(query: str, docs: List[str], *, k1: float = 1.5, b: float = 0.75) -> List[float]:
    """Standard BM25 over a small candidate set. No external deps."""
    if not docs:
        return []
    q_terms = _tokenize(query)
    if not q_terms:
        return [0.0] * len(docs)
    doc_tokens = [_tokenize(d) for d in docs]
    doc_lens = [len(t) for t in doc_tokens]
    avgdl = (sum(doc_lens) / len(doc_lens)) if doc_lens else 1.0
    n_docs = len(docs)
    # Document frequencies
    df: Dict[str, int] = {}
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    scores: List[float] = []
    for i, tokens in enumerate(doc_tokens):
        score = 0.0
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for q in q_terms:
            if q not in tf:
                continue
            idf = math.log(1 + (n_docs - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5))
            f = tf[q]
            denom = f + k1 * (1 - b + b * (doc_lens[i] / avgdl if avgdl else 1.0))
            score += idf * ((f * (k1 + 1)) / max(denom, 1e-9))
        scores.append(score)
    return scores


def _normalize(values: List[float]) -> List[float]:
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _jaccard(a: str, b: str) -> float:
    """Cheap text similarity used by MMR. We don't have direct access to
    Chroma's per-doc embeddings, so token Jaccard is a sane stand-in for
    suppressing near-duplicate snippets."""
    sa, sb = set(_tokenize(a)), set(_tokenize(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _temporal_weight(created_at: str, *, half_life_days: float = 30.0) -> float:
    """Exponential temporal decay. 1.0 at t=0, 0.5 at t=half_life, etc."""
    try:
        when = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        return 1.0
    age_days = (datetime.now(timezone.utc) - when).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _reinforcement_boost(recall_count: int) -> float:
    """Mild log-scaled boost for items the agent has actually used before.
    1.0 at 0 recalls, ~1.07 at 1, ~1.20 at 5, ~1.33 at 20."""
    return 1.0 + 0.1 * math.log1p(max(recall_count, 0))


def _apply_mmr(
    candidates: List[Dict[str, Any]],
    *,
    lam: float = 0.7,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """Maximal Marginal Relevance — pick the next candidate that maximizes
    `lam * relevance - (1-lam) * max_similarity_to_already_picked`."""
    if not candidates:
        return []
    selected: List[Dict[str, Any]] = []
    pool = list(candidates)
    # Seed with the highest-scoring item.
    pool.sort(key=lambda c: c["score"], reverse=True)
    selected.append(pool.pop(0))
    while pool and len(selected) < top_n:
        best_idx = -1
        best_val = -1e9
        for i, cand in enumerate(pool):
            sim_to_selected = max(
                (_jaccard(cand["doc"], s["doc"]) for s in selected),
                default=0.0,
            )
            val = lam * cand["score"] - (1.0 - lam) * sim_to_selected
            if val > best_val:
                best_val = val
                best_idx = i
        selected.append(pool.pop(best_idx))
    return selected


# ─────────────────────────────────────────────────────────────────────────
# MemoryStore
# ─────────────────────────────────────────────────────────────────────────


class MemoryStore:
    """Tiered long-term + short-term memory with hybrid retrieval,
    reinforcement scoring, temporal decay, MMR re-ranking, and a
    consolidation pass to merge near-duplicate session summaries."""

    # Auto-trigger consolidation after this many new session summaries.
    AUTO_CONSOLIDATE_EVERY = 50
    # Bump score for items whose 'kind' is in this set so they don't decay
    # away. Mirrors openclaw's "evergreen" concept.
    EVERGREEN_KINDS = {"pinned", "system_directive"}

    def __init__(self, db_path: Path):
        chroma_dir = db_path.parent / "chroma_memory"
        chroma_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self.short_term = ShortTermBuffer(persist_dir=db_path.parent / "short_term_memory")

        self._summaries_since_consolidate = 0
        self._last_consolidated_at: Optional[str] = None

        use_chroma = os.environ.get("USE_CHROMA", "0") == "1"
        if use_chroma:
            try:
                import chromadb
                from chromadb.utils.embedding_functions import (
                    SentenceTransformerEmbeddingFunction,
                )

                self.client = chromadb.PersistentClient(path=str(chroma_dir))
                ef = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2",
                    device="cpu",
                    normalize_embeddings=True,
                )
                self.collection = self.client.get_or_create_collection(
                    name="agent_memory",
                    embedding_function=ef,
                    metadata={"hnsw:space": "cosine"},
                )
                self._counter = self.collection.count()
                self._use_chroma = True
            except Exception:
                self.collection = _FallbackCollection()
                self._use_chroma = False
        else:
            self.collection = _FallbackCollection()
            self._use_chroma = False

    # ── Short-term helpers ──────────────────────────────────────────────

    def add_turn(self, session_id: str, content: str) -> None:
        self.short_term.add(session_id, content)

    def get_short_term(self, session_id: str) -> List[str]:
        return self.short_term.get(session_id)

    # ── Session summaries ───────────────────────────────────────────────

    def summarize_session(
        self,
        task_id: str,
        goal: str,
        success: bool,
        reason: str,
        mode: str,
        *,
        action_count: int = 0,
        tools_used: Optional[List[str]] = None,
    ) -> None:
        """Write a richer mechanical summary to long-term memory and clear
        short-term for the session. Includes provenance (task_id, mode, time)
        and behavioral hints (action count, top tools used) so future recall
        gets useful signal beyond just the goal text."""
        outcome_word = "successfully" if success else "unsuccessfully"
        reason_snippet = (reason[:200] + "…") if len(reason) > 200 else reason
        goal_snippet = (goal[:200] + "…") if len(goal) > 200 else goal

        tools_summary = ""
        if tools_used:
            # Top 5 most-frequent tools
            from collections import Counter

            top = Counter(tools_used).most_common(5)
            tools_summary = " Tools used: " + ", ".join(f"{t}×{c}" for t, c in top) + "."

        action_summary = f" {action_count} actions." if action_count else ""

        summary = (
            f"Session ({mode}): {goal_snippet}. "
            f"Completed {outcome_word}. {reason_snippet}"
            f"{action_summary}{tools_summary}"
        )
        self.add(
            kind="session_summary",
            content=summary,
            metadata={
                "task_id": task_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "success": str(success),
                "recall_count": 0,
                "action_count": int(action_count),
            },
        )
        self.short_term.clear(task_id)
        self._summaries_since_consolidate += 1

    def recall_sessions(self, query: str, n: int = 5) -> List[MemoryItem]:
        """Hybrid retrieval over session summaries:
        cosine + BM25 + temporal decay + reinforcement boost + MMR re-rank."""
        total = self.collection.count()
        if total == 0:
            return []

        # Pull a generous candidate pool, then re-rank.
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(max(n * 4, 12), total),
                where={"kind": "session_summary"},
            )
        except Exception:
            return []

        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[1.0] * len(ids)])[0]

        if not ids:
            return []

        cosine_sims = [max(0.0, 1.0 - d) for d in distances]
        bm25_raw = _bm25_scores(query, docs)
        cosine_norm = _normalize(cosine_sims)
        bm25_norm = _normalize(bm25_raw)

        candidates: List[Dict[str, Any]] = []
        for i in range(len(ids)):
            meta = metas[i] or {}
            kind = meta.get("kind", "")
            created_at = meta.get("created_at", "")
            recall_count = int(meta.get("recall_count", 0) or 0)
            t_weight = (
                1.0 if kind in self.EVERGREEN_KINDS else _temporal_weight(created_at)
            )
            r_boost = _reinforcement_boost(recall_count)
            base = 0.6 * cosine_norm[i] + 0.4 * bm25_norm[i]
            score = base * t_weight * r_boost
            candidates.append(
                {
                    "id": ids[i],
                    "doc": docs[i],
                    "meta": meta,
                    "score": score,
                }
            )

        # MMR re-rank to suppress near-duplicates.
        ranked = _apply_mmr(candidates, lam=0.7, top_n=n)

        # Reinforcement: mark these as having been recalled. Mutate both
        # the persisted metadata AND the in-flight candidate so the caller
        # sees the incremented recall_count too.
        try:
            new_metas = []
            update_ids = []
            for cand in ranked:
                meta = dict(cand["meta"])
                meta["recall_count"] = int(meta.get("recall_count", 0) or 0) + 1
                meta["last_recalled_at"] = datetime.now(timezone.utc).isoformat()
                cand["meta"] = meta  # propagate to MemoryItem returned below
                update_ids.append(cand["id"])
                new_metas.append(
                    {k: (str(v) if not isinstance(v, (str, int, float, bool)) else v) for k, v in meta.items()}
                )
            if update_ids and hasattr(self.collection, "update"):
                self.collection.update(ids=update_ids, metadatas=new_metas)
        except Exception as exc:
            _log.warning("Failed to update memory recall_count metadata: %s", exc)

        items: List[MemoryItem] = []
        for cand in ranked:
            meta = cand["meta"]
            try:
                int_id = int(cand["id"])
            except (TypeError, ValueError):
                int_id = abs(hash(cand["id"])) % (10 ** 9)
            items.append(
                MemoryItem(
                    id=int_id,
                    kind=meta.get("kind", ""),
                    content=cand["doc"],
                    metadata={
                        k: v for k, v in meta.items() if k not in ("kind", "created_at")
                    },
                    created_at=meta.get("created_at", ""),
                )
            )
        return items

    # ── Consolidation ────────────────────────────────────────────────────

    def consolidate(self, *, similarity_threshold: float = 0.88, max_age_days_for_pruning: int = 60) -> Dict[str, int]:
        """Merge near-duplicate session summaries, prune stale low-utility
        items. Safe to run repeatedly. Returns {merged, pruned, kept}."""
        total = self.collection.count()
        if total == 0:
            return {"merged": 0, "pruned": 0, "kept": 0}

        all_items = self.collection.get(limit=total, offset=0)
        ids = all_items["ids"]
        docs = all_items["documents"]
        metas = all_items["metadatas"]

        # Build groups of session_summaries for dedup
        summary_indices = [
            i for i, m in enumerate(metas) if (m or {}).get("kind") == "session_summary"
        ]

        used: set = set()
        merged_count = 0
        delete_ids: List[str] = []
        add_records: List[Tuple[str, Dict[str, Any]]] = []

        for i in summary_indices:
            if ids[i] in used:
                continue
            cluster_indices = [i]
            for j in summary_indices:
                if i == j or ids[j] in used or j in cluster_indices:
                    continue
                # Only cluster within the same mode.
                if (metas[i] or {}).get("mode") != (metas[j] or {}).get("mode"):
                    continue
                if _jaccard(docs[i], docs[j]) >= similarity_threshold:
                    cluster_indices.append(j)
            if len(cluster_indices) < 2:
                continue

            cluster_ids = [ids[k] for k in cluster_indices]
            cluster_docs = [docs[k] for k in cluster_indices]
            cluster_metas = [metas[k] or {} for k in cluster_indices]
            used.update(cluster_ids)

            # Merge: keep the longest doc as the canonical representation,
            # accumulate task_ids + sum recall_count.
            canonical_idx = max(range(len(cluster_docs)), key=lambda k: len(cluster_docs[k]))
            canonical_doc = cluster_docs[canonical_idx]
            task_ids = [m.get("task_id", "") for m in cluster_metas if m.get("task_id")]
            total_recall = sum(int(m.get("recall_count", 0) or 0) for m in cluster_metas)
            merge_count = len(cluster_ids)
            merged_doc = (
                f"{canonical_doc} [merged from {merge_count} similar sessions: "
                f"{', '.join(task_ids[:6])}{'…' if len(task_ids) > 6 else ''}]"
            )
            if len(merged_doc) > MAX_TEXT_FIELD_CHARS:
                merged_doc = merged_doc[: MAX_TEXT_FIELD_CHARS - 12] + "…(truncated)"
            merged_meta = {
                "kind": "session_summary",
                "mode": cluster_metas[canonical_idx].get("mode", ""),
                "task_id": cluster_metas[canonical_idx].get("task_id", ""),
                "merged_from": json.dumps(task_ids),
                "merge_count": merge_count,
                "recall_count": total_recall,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "consolidated": True,
            }

            delete_ids.extend(cluster_ids)
            add_records.append((merged_doc, merged_meta))
            merged_count += merge_count - 1

        # Prune stale, never-recalled, non-consolidated summaries.
        pruned_count = 0
        for i in summary_indices:
            if ids[i] in used or ids[i] in delete_ids:
                continue
            meta = metas[i] or {}
            if meta.get("consolidated"):
                continue
            recall_count = int(meta.get("recall_count", 0) or 0)
            if recall_count > 0:
                continue
            created_at = meta.get("created_at", "")
            try:
                when = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                continue
            age_days = (datetime.now(timezone.utc) - when).total_seconds() / 86400.0
            if age_days > max_age_days_for_pruning:
                delete_ids.append(ids[i])
                pruned_count += 1

        # Apply mutations
        try:
            if delete_ids:
                self.collection.delete(ids=delete_ids)
        except Exception:
            pass
        for doc, meta in add_records:
            try:
                self.add(kind="session_summary", content=doc, metadata={k: v for k, v in meta.items() if k not in ("kind", "created_at")})
            except Exception:
                continue

        self._summaries_since_consolidate = 0
        self._last_consolidated_at = datetime.now(timezone.utc).isoformat()
        return {"merged": merged_count, "pruned": pruned_count, "kept": total - len(delete_ids) + len(add_records)}

    def maybe_auto_consolidate(self) -> Optional[Dict[str, int]]:
        """Trigger a consolidation pass if enough new summaries have piled up.
        Cheap: at most a few hundred summaries when it does run."""
        if self._summaries_since_consolidate < self.AUTO_CONSOLIDATE_EVERY:
            return None
        return self.consolidate()

    # ── Stats / health ───────────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        total = self.collection.count()
        kinds: Dict[str, int] = {}
        if total:
            try:
                all_meta = self.collection.get(limit=total, offset=0)
                for m in all_meta["metadatas"]:
                    k = (m or {}).get("kind", "unknown")
                    kinds[k] = kinds.get(k, 0) + 1
            except Exception:
                kinds = {"unknown": total}
        return {
            "total_items": total,
            "by_kind": kinds,
            "use_chroma": self._use_chroma,
            "short_term_sessions": len(self.short_term.known_sessions()),
            "summaries_since_consolidate": self._summaries_since_consolidate,
            "last_consolidated_at": self._last_consolidated_at,
            "auto_consolidate_every": self.AUTO_CONSOLIDATE_EVERY,
        }

    # ── Core write / search ──────────────────────────────────────────────

    def add(self, kind: str, content: str, metadata: Dict[str, Any] | None = None) -> int:
        self._counter += 1
        doc_id = str(self._counter)
        meta = {
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
        safe_meta = {
            k: (str(v) if not isinstance(v, (str, int, float, bool)) else v)
            for k, v in meta.items()
        }
        self.collection.add(documents=[content], metadatas=[safe_meta], ids=[doc_id])
        return self._counter

    def add_action_result(self, task_id: str, action_id: str, result: str) -> int:
        idx = self.add("action_result", result, {"task_id": task_id, "action_id": action_id})
        self.enforce_sliding_window(task_id)
        return idx

    def search(self, prompt: str, limit: int = 5) -> List[MemoryItem]:
        total = self.collection.count()
        if total == 0:
            return []
        results = self.collection.query(
            query_texts=[prompt],
            n_results=min(limit, total),
        )
        items = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] or {}
            try:
                int_id = int(results["ids"][0][i])
            except (TypeError, ValueError):
                int_id = abs(hash(results["ids"][0][i])) % (10 ** 9)
            items.append(
                MemoryItem(
                    id=int_id,
                    kind=meta.get("kind", ""),
                    content=doc,
                    metadata={k: v for k, v in meta.items() if k not in ("kind", "created_at")},
                    created_at=meta.get("created_at", ""),
                )
            )
        return items

    def recent(self, limit: int = 20) -> List[MemoryItem]:
        total = self.collection.count()
        if total == 0:
            return []
        all_results = self.collection.get(
            limit=min(limit, total),
            offset=max(0, total - limit),
        )
        items = []
        for i, doc in enumerate(all_results["documents"]):
            meta = all_results["metadatas"][i] or {}
            try:
                int_id = int(all_results["ids"][i])
            except (TypeError, ValueError):
                int_id = abs(hash(all_results["ids"][i])) % (10 ** 9)
            items.append(
                MemoryItem(
                    id=int_id,
                    kind=meta.get("kind", ""),
                    content=doc,
                    metadata={k: v for k, v in meta.items() if k not in ("kind", "created_at")},
                    created_at=meta.get("created_at", ""),
                )
            )
        return list(reversed(items))

    def enforce_sliding_window(self, task_id: str):
        total = self.collection.count()
        if total == 0:
            return
        meta_results = self.collection.get(limit=total, offset=0)
        task_ids_list = []
        task_docs = []
        for i, m in enumerate(meta_results["metadatas"] or []):
            if (m or {}).get("task_id") == task_id and (m or {}).get("kind") != "session_summary":
                task_ids_list.append(meta_results["ids"][i])
                task_docs.append(meta_results["documents"][i])

        char_count = sum(len(d) for d in task_docs)
        if char_count > MAX_TEXT_FIELD_CHARS:
            half = len(task_ids_list) // 2
            oldest_ids = task_ids_list[:half]
            oldest_docs = task_docs[:half]

            summary_text = f"Summary of {len(oldest_docs)} previous actions: " + " ".join(oldest_docs)
            if len(summary_text) > MAX_TEXT_FIELD_CHARS:
                summary_text = summary_text[:MAX_TEXT_FIELD_CHARS] + "..."

            try:
                self.collection.delete(ids=oldest_ids)
                self.add("summary", summary_text, {"task_id": task_id})
            except Exception:
                pass
