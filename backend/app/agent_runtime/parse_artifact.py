"""Validate a native snapshot alongside the legacy ParseBundle envelope."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .resolved_audit import audit_resolved_tender
from .resolved_tender import ResolvedTenderV2


def finalize_native_parse_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if "resolvedTenderV2" not in bundle:
        return bundle
    result = deepcopy(bundle)
    native = ResolvedTenderV2.model_validate(result["resolvedTenderV2"]).model_dump(mode="json")
    for field in ("parseBundleId", "projectId", "workspaceId"):
        if field != "parseBundleId" and not native[field]:
            native[field] = result.get(field, "")
        if native[field] != result.get(field, ""):
            raise ValueError(f"native snapshot identity mismatch: {field}")
    outer_source = result.get("sourceFile") or {}
    for field in ("fileId", "sha256", "documentVersionId"):
        if outer_source.get(field) and native["sourceFile"].get(field) and outer_source[field] != native["sourceFile"][field]:
            raise ValueError(f"native source identity mismatch: {field}")
    native["sourceFile"] = {**native["sourceFile"], **outer_source}
    for field in ("documentBlocks", "submissionStructure", "templateFragments", "templateFragmentsArtifact"):
        if field in result:
            native[field] = deepcopy(result[field])
    native = ResolvedTenderV2.model_validate(native).model_dump(mode="json")
    result["resolvedTenderV2"] = native
    report = audit_resolved_tender(native).model_dump(mode="json")
    report["status"] = "pass" if report["readyForWriting"] else "review_required"
    previous = result.get("parseReviewReport") or {}
    if "coverage" in previous:
        report["coverage"] = previous["coverage"]
    result["parseReviewReport"] = report
    return result
