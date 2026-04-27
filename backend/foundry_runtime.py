"""Runtime helpers for Foundry file attachments and generated file links."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from agent_framework import Content
from agent_framework_foundry._agent import _FoundryAgentChatClient

logger = logging.getLogger(__name__)


def _safe_parse_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        raw_text = raw.strip()
        if not raw_text:
            return {}
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def normalize_uploaded_file_ids(raw: Any) -> dict[str, str]:
    """Normalize inbound file IDs into `{filename: file_id}`."""
    if isinstance(raw, Mapping):
        normalized: dict[str, str] = {}
        for name, file_id in raw.items():
            file_name = str(name).strip()
            file_token = str(file_id).strip()
            if file_name and file_token:
                normalized[file_name] = file_token
        return normalized

    if isinstance(raw, list):
        normalized = {}
        for index, item in enumerate(raw):
            if isinstance(item, str) and item.strip():
                normalized[f"file_{index + 1}"] = item.strip()
                continue
            if isinstance(item, Mapping):
                file_id = str(item.get("fileId") or item.get("file_id") or "").strip()
                file_name = str(item.get("filename") or item.get("name") or f"file_{index + 1}").strip()
                if file_id and file_name:
                    normalized[file_name] = file_id
        return normalized

    return {}


def extract_forwarded_props_from_session(session: Any) -> dict[str, Any]:
    """Read AG-UI forwarded props from session metadata."""
    if session is None:
        return {}
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return {}
    forwarded_raw = metadata.get("forwarded_props")
    return _safe_parse_json_object(forwarded_raw)


def _build_structured_inputs(file_ids: list[str], max_slots: int) -> dict[str, str]:
    valid_file_ids = [value for value in file_ids if value][:max_slots]
    return {
        f"file_id_{index + 1}": valid_file_ids[index] if index < len(valid_file_ids) else ""
        for index in range(max_slots)
    }


def _read_max_file_slots() -> int:
    raw = os.getenv("FOUNDRY_FILE_SLOT_COUNT", "").strip()
    if not raw:
        return 5
    try:
        value = int(raw)
    except ValueError:
        return 5
    return max(1, value)


class FileAwareFoundryChatClient(_FoundryAgentChatClient):
    """Custom Foundry chat client that injects structured file inputs per run."""

    async def _prepare_options(
        self,
        messages: Any,
        options: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        mutable_options = dict(options)
        forwarded_props = _safe_parse_json_object(mutable_options.pop("agui_forwarded_props", {}))
        include_ci_outputs = bool(mutable_options.pop("agui_include_code_interpreter_outputs", False))
        logger.info(
            "Preparing Foundry run options forwarded_props=%s include_ci_outputs=%s",
            sorted(forwarded_props.keys()),
            include_ci_outputs,
        )

        run_options = await super()._prepare_options(messages, mutable_options, **kwargs)

        file_ids = normalize_uploaded_file_ids(
            forwarded_props.get("fileIds") or forwarded_props.get("file_ids") or {}
        )
        structured_inputs = _safe_parse_json_object(
            forwarded_props.get("structuredInputs") or forwarded_props.get("structured_inputs") or {}
        )
        if file_ids:
            max_slots = _read_max_file_slots()
            structured_inputs.update(_build_structured_inputs(list(file_ids.values()), max_slots))
            logger.info(
                "Injecting uploaded files into structured_inputs file_count=%d slots=%d filenames=%s",
                len(file_ids),
                max_slots,
                list(file_ids.keys()),
            )
        if structured_inputs:
            extra_body = run_options.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
            extra_body["structured_inputs"] = structured_inputs
            run_options["extra_body"] = extra_body
            logger.debug("Structured inputs prepared keys=%s", sorted(structured_inputs.keys()))

        if include_ci_outputs:
            include = run_options.get("include")
            include_list = list(include) if isinstance(include, list) else []
            if "code_interpreter_call.outputs" not in include_list:
                include_list.append("code_interpreter_call.outputs")
            run_options["include"] = include_list
            logger.info("Enabled code interpreter output include fields=%s", include_list)

        return run_options


def _replace_sandbox_links(text: str) -> str:
    clean_text = re.sub(r"\[[^\]]+\]\(sandbox:[^)]+\)", "", text)
    clean_text = re.sub(r"\[[^\]]+\]\(/api/download\?[^\)]*\)", "", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
    return clean_text.strip()


def _extract_files_from_annotations(content: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    annotations = getattr(content, "annotations", None) or []
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        file_id = str(annotation.get("file_id") or "").strip()
        if not file_id:
            continue
        additional = annotation.get("additional_properties")
        additional_props = additional if isinstance(additional, dict) else {}
        container_id = str(additional_props.get("container_id") or "").strip()
        filename = str(additional_props.get("filename") or "output_file").strip()
        result.append(
            {
                "file_id": file_id,
                "container_id": container_id,
                "filename": filename or "output_file",
            }
        )
    return result


def _extract_files_from_code_interpreter_raw(raw_item: Any) -> list[dict[str, str]]:
    outputs = getattr(raw_item, "outputs", None)
    if outputs is None and isinstance(raw_item, dict):
        outputs = raw_item.get("outputs")

    result: list[dict[str, str]] = []
    for output in outputs or []:
        output_type = getattr(output, "type", None)
        if output_type is None and isinstance(output, dict):
            output_type = output.get("type")
        if output_type != "file":
            continue

        file_id = str(getattr(output, "file_id", None) or (output.get("file_id") if isinstance(output, dict) else "") or "").strip()
        if not file_id:
            continue
        container_id = str(
            getattr(output, "container_id", None)
            or (output.get("container_id") if isinstance(output, dict) else "")
            or ""
        ).strip()
        filename = str(
            getattr(output, "filename", None)
            or (output.get("filename") if isinstance(output, dict) else "")
            or f"output_{file_id[-8:]}.bin"
        ).strip()
        result.append(
            {
                "file_id": file_id,
                "container_id": container_id,
                "filename": filename or "output_file",
            }
        )
    return result


def _extract_generated_files_from_content(content: Any) -> list[dict[str, str]]:
    content_type = getattr(content, "type", None)
    files: list[dict[str, str]] = []

    if content_type == "hosted_file":
        file_id = str(getattr(content, "file_id", "") or "").strip()
        if file_id:
            additional = getattr(content, "additional_properties", None) or {}
            container_id = str(additional.get("container_id") or "").strip() if isinstance(additional, dict) else ""
            filename = str(additional.get("filename") or f"output_{file_id[-8:]}.bin").strip() if isinstance(additional, dict) else f"output_{file_id[-8:]}.bin"
            files.append(
                {
                    "file_id": file_id,
                    "container_id": container_id,
                    "filename": filename or "output_file",
                }
            )

    files.extend(_extract_files_from_annotations(content))

    if content_type == "code_interpreter_tool_result":
        raw_item = getattr(content, "raw_representation", None)
        if raw_item is not None:
            files.extend(_extract_files_from_code_interpreter_raw(raw_item))

    deduped: list[dict[str, str]] = []
    seen = set()
    for item in files:
        key = (item.get("file_id", ""), item.get("container_id", ""), item.get("filename", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _build_download_links_section(files: list[dict[str, str]]) -> str:
    if not files:
        return ""
    links: list[str] = []
    for item in files:
        file_id = urllib.parse.quote(item["file_id"], safe="")
        container_id = urllib.parse.quote(item.get("container_id", ""), safe="")
        filename = item.get("filename", "output_file")
        safe_filename = urllib.parse.quote(filename, safe="")
        url = f"/api/download?file_id={file_id}&container_id={container_id}&filename={safe_filename}"
        links.append(f"[Download {filename}]({url})")
    return "Generated files:\n\n" + "\n".join(links)


def attach_download_links_transform(response: Any) -> Any:
    """Append stable `/api/download` links when hosted files appear in stream updates."""
    if not hasattr(response, "with_transform_hook"):
        return response

    seen_files: set[tuple[str, str, str]] = set()
    seen_oauth_links: set[str] = set()

    def _transform(update: Any) -> Any:
        contents = getattr(update, "contents", None)
        if not isinstance(contents, list):
            return update

        discovered_files: list[dict[str, str]] = []
        pending_oauth_notices: list[Content] = []
        for content in contents:
            if getattr(content, "type", None) == "text" and isinstance(getattr(content, "text", None), str):
                content.text = _replace_sandbox_links(content.text)
            if getattr(content, "type", None) == "oauth_consent_request":
                consent_link = str(getattr(content, "consent_link", "") or "").strip()
                if consent_link and consent_link not in seen_oauth_links:
                    seen_oauth_links.add(consent_link)
                    logger.info("MCP OAuth consent required link_detected=true")
                    pending_oauth_notices.append(
                        Content.from_text(
                            text=(
                                "MCP authorization is required for this tool call.\n\n"
                                f"[Authorize MCP access]({consent_link})"
                            )
                        )
                    )
            discovered_files.extend(_extract_generated_files_from_content(content))

        if pending_oauth_notices:
            contents.extend(pending_oauth_notices)

        new_files: list[dict[str, str]] = []
        for item in discovered_files:
            key = (item["file_id"], item.get("container_id", ""), item.get("filename", "output_file"))
            if key in seen_files:
                continue
            seen_files.add(key)
            new_files.append(item)

        links_section = _build_download_links_section(new_files)
        if links_section:
            logger.info("Generated files detected count=%d files=%s", len(new_files), [item.get("filename") for item in new_files])
            contents.append(Content.from_text(text=links_section))

        return update

    return response.with_transform_hook(_transform)
