"""Node 2 — Research Agent. Fires parallel source fetchers via ThreadPoolExecutor.
Now includes canonical medical sources (UMLS, RxNorm, DailyMed) alongside existing sources.
"""
import time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from sources.europe_pmc import fetch_europe_pmc
from sources.clinical_trials import fetch_clinical_trials
from sources.wikipedia import fetch_wikipedia
from sources.openalex import fetch_openalex
from sources.pubmed import fetch_pubmed
from sources.semantic_scholar import fetch_semantic_scholar
from sources.reddit_public import fetch_reddit
from sources.tavily_search import fetch_tavily
from sources.canonical.umls import fetch_umls
from sources.canonical.rxnorm import fetch_rxnorm
from sources.canonical.dailymed import fetch_dailymed
from utils.cluster import deduplicate
from utils.mmr import mmr_select

TIMEOUT_FAST = 4
TIMEOUT_DEEP = 12


def research_node(state: dict) -> dict:
    timeout = TIMEOUT_FAST if state["mode"] == "fast" else TIMEOUT_DEEP
    subqueries = state.get("subqueries", [])

    if not subqueries:
        state["raw_snippets"] = []
        state["clustered_snippets"] = []
        state["sources_checked"] = 0
        state["rate_limited_sources"] = set()
        state["sse_log"] = state.get("sse_log", []) + ["[research] no subqueries — skipped"]
        return state

    rate_limited: set[str] = set()
    lock = threading.Lock()
    all_snippets: list[dict] = []

    def safe_fetch(fn, source_name, *args):
        if source_name in rate_limited:
            return []
        try:
            return fn(*args)
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                with lock:
                    rate_limited.add(source_name)
            return []

    # check for medications in patient history (triggers rxnorm/dailymed)
    history = state.get("patient_history_timeline", [])
    has_medications = any(e.get("event_type") == "medication" for e in history)

    tasks = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        for sq in subqueries:
            q = sq["search_query"]

            # tier 0 — canonical sources (always fire)
            tasks.append(executor.submit(safe_fetch, fetch_umls, "umls", q))

            # tier 1 — literature (always fire)
            tasks.append(executor.submit(safe_fetch, fetch_europe_pmc, "europe_pmc", q))
            tasks.append(executor.submit(safe_fetch, fetch_wikipedia, "wikipedia", q))
            tasks.append(executor.submit(safe_fetch, fetch_pubmed, "pubmed", q))
            tasks.append(executor.submit(safe_fetch, fetch_reddit, "reddit", q))

            # tier 2 — deep mode or medication-aware
            if state["mode"] == "deep":
                tasks.append(executor.submit(safe_fetch, fetch_clinical_trials, "clinicaltrials", q))
                tasks.append(executor.submit(safe_fetch, fetch_openalex, "openalex", q))
                tasks.append(executor.submit(safe_fetch, fetch_semantic_scholar, "semantic_scholar", q))
                tasks.append(executor.submit(safe_fetch, fetch_tavily, "tavily", q))
                tasks.append(executor.submit(safe_fetch, fetch_rxnorm, "rxnorm", q))

            # dailymed — only if medications in history or deep mode
            if has_medications or state["mode"] == "deep":
                tasks.append(executor.submit(safe_fetch, fetch_dailymed, "dailymed", q))

        for future in as_completed(tasks, timeout=timeout * max(len(tasks), 1)):
            try:
                snippets = future.result(timeout=timeout)
                all_snippets.extend(snippets or [])
            except TimeoutError:
                pass
            except Exception:
                pass

    state["raw_snippets"] = all_snippets
    state["sources_checked"] = len(all_snippets)
    state["rate_limited_sources"] = rate_limited

    # dedup then truncate via mmr
    deduped = deduplicate(all_snippets)
    state["clustered_snippets"] = mmr_select(deduped, k=10, lambda_=0.5)

    # compute source tier breakdown
    tier_counts = {"canonical": 0, "literature": 0, "contextual": 0}
    for s in state["clustered_snippets"]:
        tier = s.get("source_tier", "contextual")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    log = state.get("sse_log", [])
    log.append(
        f"[research] {len(all_snippets)} raw snippets · {len(deduped)} after dedup · "
        f"{len(state['clustered_snippets'])} after MMR · "
        f"tiers: {tier_counts['canonical']}C/{tier_counts['literature']}L/{tier_counts['contextual']}X · "
        f"skipped sources: {rate_limited or 'none'}"
    )
    state["sse_log"] = log
    return state
