from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import core_foundry
from pbi_fabric_mcp_client import (
    ClientConfig,
    ToolCallOutput,
    build_config_from_env,
    call_tool_sync,
)

logger = logging.getLogger("pbi_orchestrator")

GENERATE_QUERY_DEVELOPER_GUIDANCE = (
    "You are generating DAX for Power BI semantic model queries. "
    "Use GetSemanticModelSchema context first, prefer explicit filters for requested entities/time, "
    "and ensure the output DAX is executable with a single EVALUATE statement."
)

_DAX_HISTORY_CACHE: dict[str, list[dict[str, str]]] = {}
_DAX_HISTORY_LOCK = threading.Lock()

SCHEMA_KEYWORDS = {
    "schema",
    "semantic model",
    "semantic",
    "table",
    "tables",
    "column",
    "columns",
    "measure",
    "measures",
    "field",
    "fields",
    "\u6b04\u4f4d",  # 欄位
    "\u8cc7\u6599\u8868",  # 資料表
    "\u6a21\u578b\u7d50\u69cb",  # 模型結構
    "\u8a9e\u610f\u6a21\u578b",  # 語意模型
    "\u6e2c\u5ea6",  # 測度
}

DATA_KEYWORDS = {
    "power bi",
    "fabric",
    "dataset",
    "dax",
    "query",
    "execute",
    "top",
    "rank",
    "yoy",
    "mom",
    "mtd",
    "ytd",
    "revenue",
    "share",
    "profit",
    "gm",
    "performance",
    "\u591a\u5c11",  # 多少
    "\u4f54\u6bd4",  # 占比/佔比
    "\u4f54\u5e7e%",  # 占幾%
    "\u6bd4\u4f8b",  # 比例
    "\u71df\u6536",  # 營收
    "\u6536\u5165",  # 收入
    "\u6392\u540d",  # 排名
    "\u524d\u5e7e",  # 前幾
    "\u67e5\u8a62",  # 查詢
    "\u5e74",  # 年
}

EXPLAIN_KEYWORDS = {
    "explain",
    "summary",
    "summarize",
    "\u89e3\u91cb",  # 解釋
    "\u8aaa\u660e",  # 說明
    "\u4ecb\u7d39",  # 介紹
    "\u5831\u8868\u5728\u5e79\u561b",  # 報表在幹嘛
}

QUERY_TOKEN_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "what",
    "which",
    "show",
    "please",
    "\u8acb",
    "\u544a\u8a34\u6211",
    "\u8acb\u554f",
    "\u4ee5\u53ca",
    "\u8a72\u5e74",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _to_lower(text: str) -> str:
    return (text or "").strip().lower()


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_schema_intent(user_text: str) -> bool:
    text = _to_lower(user_text)
    return _contains_any(text, SCHEMA_KEYWORDS)


def _is_data_intent(user_text: str) -> bool:
    text = _to_lower(user_text)
    if _contains_any(text, DATA_KEYWORDS):
        return True

    # Strong numeric/query signals
    if re.search(r"(19|20)\d{2}", text):
        return True
    if re.search(r"\btop\s*\d+\b", text):
        return True
    if re.search(r"[\w\)]\s*[%％]", text):
        return True
    if re.search(r"\b[a-z]{1,5}\d{2,}\b", text):
        return True
    if re.search(r"[\u4e00-\u9fff]+\s*\d+", text):
        return True

    return False


def _is_explain_intent(user_text: str) -> bool:
    text = _to_lower(user_text)
    return _contains_any(text, EXPLAIN_KEYWORDS)


def _extract_meta_intent_hint(runtime_meta: dict[str, Any]) -> str:
    pbi_hints = runtime_meta.get("pbiHints")
    if not isinstance(pbi_hints, dict):
        return ""
    hint = str(pbi_hints.get("intentHint") or "").strip().lower()
    if hint in {"schema", "query", "foundry_chat"}:
        return hint
    return ""


def _decide_route(user_text: str, runtime_meta: Optional[dict[str, Any]] = None) -> str:
    normalized_meta = _normalize_runtime_meta(runtime_meta)
    schema_intent = _is_schema_intent(user_text)
    data_intent = _is_data_intent(user_text)
    explain_intent = _is_explain_intent(user_text)
    meta_hint = _extract_meta_intent_hint(normalized_meta)

    if schema_intent and not data_intent:
        return "schema"
    if data_intent:
        return "query"
    if explain_intent:
        return "foundry_chat"
    if meta_hint:
        return meta_hint

    # PBI dedicated assistant default route: query
    default_to_query = _env_bool("PBI_ORCH_DEFAULT_TO_QUERY", True)
    return "query" if default_to_query else "foundry_chat"


def _extract_primary_text(call: ToolCallOutput) -> str:
    raw = call.raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_val = str(item.get("text") or "").strip()
                    if text_val:
                        chunks.append(text_val)
            if chunks:
                return "\n".join(chunks)
    return call.output_text.strip()


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def _summarize_schema(
    call: ToolCallOutput,
    *,
    max_tables: int = 12,
    include_columns: bool = False,
    include_measures: bool = False,
    max_columns_per_table: int = 8,
    max_measures_per_table: int = 8,
) -> str:
    text = _extract_primary_text(call)
    payload = _extract_json_object(text)
    if not payload:
        return text[:4000]

    schema = payload.get("schema") if isinstance(payload, dict) else None
    tables = schema.get("Tables") if isinstance(schema, dict) else None
    model = payload.get("semanticModel") if isinstance(payload, dict) else None

    if not isinstance(tables, list):
        return text[:4000]

    lines: list[str] = []
    if isinstance(model, dict):
        model_name = str(model.get("Name") or "").strip()
        model_url = str(model.get("Url") or "").strip()
        if model_name:
            lines.append(f"Model: {model_name}")
        if model_url:
            lines.append(f"URL: {model_url}")

    lines.append(f"Table count: {len(tables)}")
    lines.append("")

    for table in tables[:max_tables]:
        if not isinstance(table, dict):
            continue

        name = str(table.get("Name") or "").strip() or "<unnamed>"
        columns = table.get("Columns") if isinstance(table.get("Columns"), list) else []
        measures = table.get("Measures") if isinstance(table.get("Measures"), list) else []

        col_names = [
            str(c.get("Name"))
            for c in columns[:max_columns_per_table]
            if isinstance(c, dict) and c.get("Name")
        ]
        measure_names = [
            str(m.get("Name"))
            for m in measures[:max_measures_per_table]
            if isinstance(m, dict) and m.get("Name")
        ]

        lines.append(f"- {name} | columns={len(columns)} | measures={len(measures)}")
        if include_columns and col_names:
            lines.append(f"  columns: {', '.join(col_names)}")
        if include_measures and measure_names:
            lines.append(f"  measures: {', '.join(measure_names)}")

    if len(tables) > max_tables:
        lines.append(f"... and {len(tables) - max_tables} more tables")

    return "\n".join(lines)[:6000]


def _extract_dax_query(call: ToolCallOutput) -> str:
    text = _extract_primary_text(call)
    payload = _extract_json_object(text)
    if payload:
        for key in ("daxQuery", "query", "dax"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    fenced = re.findall(r"```(?:dax)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    for block in fenced:
        if "EVALUATE" in block.upper():
            return block.strip()

    match = re.search(r"(EVALUATE[\s\S]+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return ""


def _truncate(value: str, limit: int = 12000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _normalize_runtime_meta(runtime_meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(runtime_meta, dict):
        return runtime_meta
    return {}


def _extract_meta_value_terms(runtime_meta: dict[str, Any]) -> list[str]:
    pbi_hints = runtime_meta.get("pbiHints")
    if not isinstance(pbi_hints, dict):
        return []
    raw_terms = pbi_hints.get("valueSearchTerms")
    if not isinstance(raw_terms, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_terms:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized[:20]


def _extract_meta_schema_selection(runtime_meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    pbi_hints = runtime_meta.get("pbiHints")
    if not isinstance(pbi_hints, dict):
        return None
    raw = pbi_hints.get("schemaSelection")
    if not isinstance(raw, dict):
        return None
    tables = raw.get("tables")
    if not isinstance(tables, list):
        return None
    cleaned_tables: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or "").strip()
        if not name:
            continue
        columns = table.get("columns")
        measures = table.get("measures")
        cleaned_tables.append(
            {
                "name": name,
                "columns": columns if isinstance(columns, list) else None,
                "measures": measures if isinstance(measures, list) else None,
            }
        )
    if not cleaned_tables:
        return None
    return {"tables": cleaned_tables}


def _combine_value_terms(primary_terms: list[str], meta_terms: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in (primary_terms, meta_terms):
        for term in source:
            value = str(term or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(value)
    return merged[:20]


def _combine_schema_selection(
    auto_selection: Optional[dict[str, Any]],
    meta_selection: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not auto_selection and not meta_selection:
        return None
    if auto_selection and not meta_selection:
        return auto_selection
    if meta_selection and not auto_selection:
        return meta_selection

    auto_tables = auto_selection.get("tables") if isinstance(auto_selection, dict) else []
    meta_tables = meta_selection.get("tables") if isinstance(meta_selection, dict) else []
    if not isinstance(auto_tables, list):
        auto_tables = []
    if not isinstance(meta_tables, list):
        meta_tables = []

    merged_tables: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in meta_tables + auto_tables:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        merged_tables.append(table)

    if not merged_tables:
        return None
    return {"tables": merged_tables[:15]}


def _append_dax_history(session_key: Optional[str], user_text: str, dax_query: str) -> None:
    key = (session_key or "").strip()
    if not key:
        return
    with _DAX_HISTORY_LOCK:
        history = _DAX_HISTORY_CACHE.get(key, [])
        history.append({"role": "User", "content": user_text.strip()})
        history.append({"role": "Assistant", "content": dax_query.strip()})
        _DAX_HISTORY_CACHE[key] = history[-16:]


def _get_dax_history(session_key: Optional[str]) -> list[dict[str, str]]:
    key = (session_key or "").strip()
    if not key:
        return []
    with _DAX_HISTORY_LOCK:
        history = _DAX_HISTORY_CACHE.get(key, [])
    return [dict(item) for item in history if isinstance(item, dict)]


def _normalize_chat_history(chat_history: Optional[list[dict[str, str]]], current_user_text: str) -> list[dict[str, str]]:
    if not isinstance(chat_history, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in chat_history:
        if not isinstance(item, dict):
            continue
        raw_role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not raw_role or not content:
            continue
        role_map = {
            "user": "User",
            "assistant": "Assistant",
        }
        role = role_map.get(raw_role)
        if not role:
            continue
        if role == "User" and content == current_user_text.strip():
            continue
        normalized.append({"role": role, "content": content})

    return normalized[-12:]


def _extract_schema_tables(schema_call: ToolCallOutput) -> list[dict[str, Any]]:
    text = _extract_primary_text(schema_call)
    payload = _extract_json_object(text)
    if not payload or not isinstance(payload, dict):
        return []

    schema = payload.get("schema")
    if not isinstance(schema, dict):
        return []

    tables = schema.get("Tables")
    if not isinstance(tables, list):
        return []

    return [t for t in tables if isinstance(t, dict)]


def _tokenize_query_terms(user_text: str) -> list[str]:
    raw_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}|[0-9]{2,}|[\u4e00-\u9fff]{2,}", user_text or "")
    lowered: list[str] = []
    for token in raw_tokens:
        value = token.strip().lower()
        if not value or value in QUERY_TOKEN_STOPWORDS or len(value) <= 1:
            continue
        lowered.append(value)

    seen: set[str] = set()
    deduped: list[str] = []
    for token in lowered:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _extract_value_search_terms(user_text: str) -> list[str]:
    text = user_text or ""
    terms: list[str] = []

    patterns = [
        r"\b[A-Z]{1,6}\d{2,}\b",  # e.g. U01200
        r"\b\d{4}\b",  # year
        r"[\u4e00-\u9fff]{2,8}",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, text):
            token = str(match).strip()
            if not token:
                continue
            if token in {"\u591a\u5c11", "\u4ee5\u53ca", "\u8acb\u554f", "\u8a72\u5e74"}:
                continue
            terms.append(token)

    seen: set[str] = set()
    deduped: list[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)

    return deduped[:12]


def _build_schema_selection(
    schema_call: ToolCallOutput,
    user_text: str,
    *,
    max_tables: int = 10,
    max_columns_per_table: int = 12,
    max_measures_per_table: int = 12,
) -> Optional[dict[str, Any]]:
    tables = _extract_schema_tables(schema_call)
    if not tables:
        return None

    terms = _tokenize_query_terms(user_text)
    scored: list[tuple[int, dict[str, Any]]] = []

    for table in tables:
        table_name = str(table.get("Name") or "").strip()
        if not table_name:
            continue

        columns = table.get("Columns") if isinstance(table.get("Columns"), list) else []
        measures = table.get("Measures") if isinstance(table.get("Measures"), list) else []

        col_names = [
            str(c.get("Name")).strip()
            for c in columns
            if isinstance(c, dict) and c.get("Name")
        ]
        measure_names = [
            str(m.get("Name")).strip()
            for m in measures
            if isinstance(m, dict) and m.get("Name")
        ]

        table_name_l = table_name.lower()
        score = 0
        for term in terms:
            if term in table_name_l or table_name_l in term:
                score += 6
            score += sum(2 for name in col_names if term in name.lower())
            score += sum(3 for name in measure_names if term in name.lower())

        if re.search(r"(date|time|year|month|\u5e74|\u6708|\u65e5)", table_name_l):
            score += 1

        scored.append(
            (
                score,
                {
                    "name": table_name,
                    "columns": col_names[:max_columns_per_table] or None,
                    "measures": measure_names[:max_measures_per_table] or None,
                },
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)

    if any(score > 0 for score, _ in scored):
        selected_tables = [table for score, table in scored if score > 0][:max_tables]
    else:
        selected_tables = [table for _, table in scored[:max_tables]]

    if not selected_tables:
        return None

    return {"tables": selected_tables}


def _generate_query_with_retry(
    user_text: str,
    config: ClientConfig,
    *,
    schema_selection: Optional[dict[str, Any]],
    value_terms: list[str],
    generate_chat_history: list[dict[str, str]],
) -> tuple[ToolCallOutput, dict[str, Any], bool]:
    """Call GenerateQuery once, then retry with minimal args if needed."""
    primary_args: dict[str, Any] = {"userInput": user_text}
    if schema_selection:
        primary_args["schemaSelection"] = schema_selection
    if value_terms:
        primary_args["valueSearchTerms"] = value_terms
    if generate_chat_history:
        primary_args["chatHistory"] = generate_chat_history

    logger.info(
        "[PBI_ORCH] GenerateQuery primary call | has_schema_selection=%s | value_terms=%s | chat_history_count=%d",
        bool(schema_selection),
        ",".join(value_terms),
        len(generate_chat_history),
    )
    primary_call = call_tool_sync("GenerateQuery", primary_args, config=config)
    if not primary_call.is_error:
        return primary_call, primary_args, False

    logger.warning(
        "[PBI_ORCH] GenerateQuery primary failed, retry minimal args | error=%s",
        _truncate(_extract_primary_text(primary_call), 800),
    )
    error_text = _to_lower(_extract_primary_text(primary_call))
    chat_history_invalid = "invalid chat history" in error_text

    fallback_args: dict[str, Any] = {"userInput": user_text}
    if generate_chat_history and not chat_history_invalid:
        fallback_args["chatHistory"] = generate_chat_history

    logger.info(
        "[PBI_ORCH] GenerateQuery retry | drop_chat_history=%s",
        chat_history_invalid,
    )
    retry_call = call_tool_sync("GenerateQuery", fallback_args, config=config)
    return retry_call, fallback_args, True


def _finalize_with_foundry(
    *,
    user_text: str,
    agent_name: str,
    session_key: Optional[str],
    context_blocks: List[str],
) -> Tuple[str, List[dict]]:
    context = "\n\n".join([block for block in context_blocks if block.strip()])
    prompt = (
        "You are an enterprise Power BI analyst.\n"
        "Use the provided tool results to answer in Traditional Chinese.\n"
        "Be concrete, concise, and explicit about missing data assumptions.\n\n"
        f"User question:\n{user_text}\n\n"
        f"Tool results:\n{context}"
    )
    return core_foundry.ask_foundry_agent(
        prompt,
        agent_name,
        {},
        None,
        session_key=session_key,
    )


def _run_schema_flow(
    user_text: str,
    agent_name: str,
    session_key: Optional[str],
    config: ClientConfig,
) -> Tuple[str, List[dict]]:
    started = time.perf_counter()
    schema_call = call_tool_sync("GetSemanticModelSchema", {}, config=config)
    if schema_call.is_error:
        return (
            "GetSemanticModelSchema failed:\n\n" + _truncate(_extract_primary_text(schema_call), 4000),
            [],
        )

    text_l = _to_lower(user_text)
    ask_columns = any(token in text_l for token in ("column", "columns", "field", "fields", "\u6b04", "\u6b04\u4f4d"))
    ask_measures = any(token in text_l for token in ("measure", "measures", "\u6e2c\u5ea6", "\u6307\u6a19"))
    max_tables = int(os.getenv("PBI_ORCH_SCHEMA_MAX_TABLES", "12"))

    summary = _summarize_schema(
        schema_call,
        max_tables=max_tables,
        include_columns=ask_columns,
        include_measures=ask_measures,
    )
    use_foundry_for_schema = _env_bool("PBI_ORCH_SCHEMA_USE_FOUNDRY", False)

    logger.info(
        "[PBI_ORCH] route=schema | use_foundry=%s | elapsed=%.2fs",
        use_foundry_for_schema,
        time.perf_counter() - started,
    )

    if not use_foundry_for_schema:
        return summary, []

    try:
        return _finalize_with_foundry(
            user_text=user_text,
            agent_name=agent_name,
            session_key=session_key,
            context_blocks=["Tool: GetSemanticModelSchema", summary],
        )
    except Exception:
        return summary, []


def _run_query_flow(
    user_text: str,
    agent_name: str,
    session_key: Optional[str],
    config: ClientConfig,
    runtime_meta: Optional[dict[str, Any]] = None,
    chat_history: Optional[list[dict[str, str]]] = None,
) -> Tuple[str, List[dict]]:
    started = time.perf_counter()
    normalized_meta = _normalize_runtime_meta(runtime_meta)

    schema_call = call_tool_sync("GetSemanticModelSchema", {}, config=config)
    if schema_call.is_error:
        return (
            "GetSemanticModelSchema failed:\n\n" + _truncate(_extract_primary_text(schema_call), 5000),
            [],
        )

    auto_schema_selection = _build_schema_selection(
        schema_call,
        user_text,
        max_tables=int(os.getenv("PBI_ORCH_QUERY_SCHEMA_TABLES", "10")),
    )
    meta_schema_selection = _extract_meta_schema_selection(normalized_meta)
    schema_selection = _combine_schema_selection(auto_schema_selection, meta_schema_selection)

    extracted_terms = _extract_value_search_terms(user_text)
    meta_terms = _extract_meta_value_terms(normalized_meta)
    value_terms = _combine_value_terms(extracted_terms, meta_terms)

    generate_chat_history: list[dict[str, str]] = []
    generate_chat_history.append(
        {
            "role": "Assistant",
            "content": GENERATE_QUERY_DEVELOPER_GUIDANCE,
        }
    )
    generate_chat_history.extend(_get_dax_history(session_key))
    generate_chat_history.extend(_normalize_chat_history(chat_history, user_text))
    generate_chat_history = generate_chat_history[-20:]

    generate_call, generate_args, retried_minimal = _generate_query_with_retry(
        user_text,
        config,
        schema_selection=schema_selection,
        value_terms=value_terms,
        generate_chat_history=generate_chat_history,
    )
    if generate_call.is_error:
        logger.error(
            "[PBI_ORCH] route=query | GenerateQuery failed after retry=%s | error=%s",
            retried_minimal,
            _truncate(_extract_primary_text(generate_call), 1500),
        )
        return (
            "GenerateQuery failed:\n\n" + _truncate(_extract_primary_text(generate_call), 5000),
            [],
        )

    dax_query = _extract_dax_query(generate_call)
    if not dax_query:
        logger.error(
            "[PBI_ORCH] route=query | DAX parse failed | generate_args_keys=%s | output_preview=%s",
            ",".join(sorted(generate_args.keys())),
            _truncate(_extract_primary_text(generate_call), 1200),
        )
        return (
            "Could not parse DAX from GenerateQuery output.\n\n"
            + _truncate(_extract_primary_text(generate_call), 8000),
            [],
        )

    execute_call = call_tool_sync(
        "ExecuteQuery",
        {
            "daxQuery": dax_query,
            "maxRows": int(os.getenv("PBI_ORCH_QUERY_MAX_ROWS", "250")),
        },
        config=config,
    )
    if execute_call.is_error:
        logger.error(
            "[PBI_ORCH] route=query | ExecuteQuery failed | dax_preview=%s | error=%s",
            _truncate(dax_query, 500),
            _truncate(_extract_primary_text(execute_call), 1200),
        )
        return (
            "ExecuteQuery failed.\n\n"
            f"DAX:\n{dax_query}\n\n"
            f"Error:\n{_truncate(_extract_primary_text(execute_call), 5000)}",
            [],
        )

    schema_summary = _summarize_schema(schema_call)
    execute_text = _extract_primary_text(execute_call)
    _append_dax_history(session_key, user_text, dax_query)

    logger.info(
        "[PBI_ORCH] route=query | elapsed=%.2fs | dax_len=%d | execute_len=%d | value_terms=%s | retried_minimal=%s | chat_history_count=%d",
        time.perf_counter() - started,
        len(dax_query),
        len(execute_text),
        ",".join(value_terms),
        retried_minimal,
        len(generate_chat_history),
    )

    try:
        return _finalize_with_foundry(
            user_text=user_text,
            agent_name=agent_name,
            session_key=session_key,
            context_blocks=[
                "Tool: GetSemanticModelSchema (summary)",
                _truncate(schema_summary, 6000),
                "Tool: GenerateQuery (DAX)",
                dax_query,
                "Tool: ExecuteQuery (result)",
                _truncate(execute_text, 8000),
            ],
        )
    except Exception:
        fallback = f"DAX:\n{dax_query}\n\nExecuteQuery result:\n{_truncate(execute_text, 8000)}"
        return fallback, []


def handle_user_message(
    *,
    user_text: str,
    agent_name: str,
    attached_files_dict: Optional[Dict[str, str]] = None,
    session_key: Optional[str] = None,
    runtime_meta: Optional[Dict[str, Any]] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[dict]]:
    attached_files_dict = attached_files_dict or {}
    normalized_meta = _normalize_runtime_meta(runtime_meta)

    if attached_files_dict:
        logger.info("[PBI_ORCH] route=foundry_file")
        return core_foundry.ask_foundry_agent(
            user_text,
            agent_name,
            attached_files_dict,
            None,
            session_key=session_key,
        )

    route = _decide_route(user_text, normalized_meta)
    logger.info(
        "[PBI_ORCH] route_decision=%s | schema_intent=%s | data_intent=%s | explain_intent=%s | meta_keys=%s | chat_history_count=%d",
        route,
        _is_schema_intent(user_text),
        _is_data_intent(user_text),
        _is_explain_intent(user_text),
        ",".join(sorted(normalized_meta.keys())),
        len(chat_history or []),
    )

    if route == "foundry_chat":
        logger.info("[PBI_ORCH] route=foundry_chat")
        return core_foundry.ask_foundry_agent(
            user_text,
            agent_name,
            attached_files_dict,
            None,
            session_key=session_key,
        )

    config = build_config_from_env(verbose=False)

    try:
        if route == "schema":
            return _run_schema_flow(user_text, agent_name, session_key, config)
        return _run_query_flow(
            user_text,
            agent_name,
            session_key,
            config,
            runtime_meta=normalized_meta,
            chat_history=chat_history,
        )
    except Exception as exc:
        logger.exception("[PBI_ORCH] sidecar failed, fallback to Foundry only: %s", exc)
        fallback_prompt = (
            f"User question: {user_text}\n"
            f"MCP sidecar failed: {exc}\n"
            "Please provide the best possible answer and ask user to retry."
        )
        return core_foundry.ask_foundry_agent(
            fallback_prompt,
            agent_name,
            attached_files_dict,
            None,
            session_key=session_key,
        )
