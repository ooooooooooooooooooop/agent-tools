"""Deterministic, evidence-first reconciliation of DSH model capabilities.

Inputs are supplied mappings/files; this module never changes canonical state or
makes routing decisions.  Provider-qualified route identity is retained while
capability families are deduplicated by resolved model identity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


ISSUES = (
    "OUTPUT_AS_CONTEXT", "ACCIDENTAL_262144_FALLBACK", "STALE_CONTEXT_METADATA",
    "ALIAS_CAPABILITY_MISMATCH", "ALIAS_MISMATCH", "OVERSTATED_CONTEXT",
    "ROUTER_CLAMP", "OUTPUT_LIMIT_MISMATCH", "CAPABILITY_SOURCE_DIVERGENCE",
    "MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE", "UNAVAILABLE_ROUTE",
    "PROVIDER_MODEL_MISSING", "ROUTE_NOT_ADMITTED", "SPECIALIZED_MODALITY", "CAPABILITY_NOT_APPLICABLE",
    "OUTPUT_SEMANTICS_DYNAMIC",
)


def _load(value: Any) -> Any:
    if isinstance(value, (str, Path)):
        path = Path(value)
        with path.open("r", encoding="utf-8-sig") as fh:
            return yaml.safe_load(fh) if path.suffix in {".yaml", ".yml"} else json.load(fh)
    return value


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def normalize_identity(item: Mapping[str, Any], *, category: str = "route") -> dict[str, Any]:
    """Normalize requested, variant, alias, and resolved provider identity."""
    provider = _first(item, "provider", "provider_id", "backend") or "unknown"
    route = _first(item, "route", "route_id", "name", "id") or "unknown"
    variant = _first(item, "variant", "variant_id", "model", "model_id") or route
    resolved = _first(item, "resolved", "resolved_id", "resolvedModel", "upstreamModel", "model") or variant
    alias = _first(item, "alias", "alias_id")
    return {
        "provider": str(provider), "route": str(route), "variant": str(variant),
        "resolved": str(resolved), "alias": str(alias) if alias is not None else None,
        "category": category,
    }


def capability_family(identity: Mapping[str, Any]) -> str:
    """Provider-qualified resolved family; cross-provider aliases need evidence."""
    provider = str(identity.get("provider") or "unknown").lower()
    resolved = str(identity.get("resolved") or identity.get("variant") or identity.get("route")).lower()
    return f"{provider}:{resolved}"


def _iter_entries(value: Any, category: str) -> Iterable[tuple[dict[str, Any], str]]:
    value = _load(value) or {}
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, Mapping):
                yield dict(item), f"{category}[{index}]"
        return
    if not isinstance(value, Mapping):
        return
    for wrapper in ("models", "routes", "providers", "presets", "pressure", "pressure_routes", "items"):
        if wrapper in value:
            yield from _iter_entries(value[wrapper], category)
            return
    for key in sorted(value, key=str):
        item = value[key]
        if isinstance(item, Mapping):
            row = dict(item)
            row.setdefault("id", key)
            yield row, f"{category}.{key}"


def inventory_routes(*, admitted=None, providers=None, routing=None, presets=None,
                     pressure=None, sources: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Inventory route rows while retaining source and provider-qualified identity."""
    values = sources or {"admitted": admitted, "providers": providers, "routing": routing,
                         "presets": presets, "pressure": pressure}
    out = []
    for category, source in values.items():
        if source is None:
            continue
        for item, evidence in _iter_entries(source, category):
            identity = normalize_identity(item, category=category)
            row = {**identity, "family": capability_family(identity),
                   "metadata": dict(item), "provenance": [evidence]}
            row["available"] = item.get("available", item.get("status") not in {"unavailable", "disabled"})
            out.append(row)
    return sorted(out, key=lambda r: (r["family"], r["provider"], r["route"], r["variant"], r["category"]))


def _merge(routes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in routes:
        key = (row["provider"], row["route"], row["variant"], row["resolved"])
        if key not in merged:
            merged[key] = {**row, "provenance": sorted(set(row.get("provenance", []))),
                           "categories": [row["category"]]}
            continue
        dst = merged[key]
        dst["provenance"] = sorted(set(dst["provenance"]) | set(row.get("provenance", [])))
        dst["categories"] = sorted(set(dst["categories"]) | {row["category"]})
        for key, value in row.get("metadata", {}).items():
            if value is not None:
                dst["metadata"][key] = value
        dst["available"] = bool(dst["available"] or row.get("available"))
    return sorted(merged.values(), key=lambda r: (r["family"], r["provider"], r["route"], r["variant"], r["resolved"]))


def effective_limits(metadata: Mapping[str, Any], provider: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return configured/effective context and output limits with reserve.

    An attested provider limit is an upper clamp.  A separately named
    ``contextWindowClampMin`` is a lower floor used only by synthetic fixtures.
    """
    provider = provider or {}
    context = _first(metadata, "contextWindow", "context_window", "max_input_tokens", "context")
    output = _first(metadata, "maxOutputTokens", "max_output_tokens", "outputLimit", "max_tokens")
    attested = _first(provider, "providerAttestedLimit", "provider_attested_limit", "contextWindowClamp", "context_window_clamp")
    floor = _first(provider, "contextWindowClampMin", "context_window_clamp_min", "minContextWindow")
    explicit_reserve = _first(metadata, "reservedOutputTokens", "reserved_output_tokens", "outputReserve")
    if explicit_reserve is None:
        explicit_reserve = _first(provider, "reservedOutputTokens", "reserved_output_tokens", "outputReserve")
    reserve_val = explicit_reserve if explicit_reserve is not None else 0
    if isinstance(context, (int, float)) and isinstance(attested, (int, float)):
        context = min(context, attested)
    if isinstance(context, (int, float)) and isinstance(floor, (int, float)):
        context = max(context, floor)
    usable = context - reserve_val if isinstance(context, (int, float)) else None
    if isinstance(output, (int, float)) and isinstance(usable, (int, float)):
        output = min(output, usable)
    reserve_repr = explicit_reserve if explicit_reserve is not None else "REQUEST_DYNAMIC"
    result = {"context_window": context, "max_output_tokens": output,
              "reserved_output_tokens": reserve_repr, "usable_input_tokens": usable}
    if attested is not None:
        result["provider_attested_limit"] = attested
    return result


def _truth(metadata: Mapping[str, Any]) -> tuple[Any, Any]:
    evidence = metadata.get("capacityEvidence") or metadata.get("evidence")
    if isinstance(evidence, Mapping):
        return (_first(evidence, "contextWindow", "context_window", "max_input_tokens"),
                _first(evidence, "maxOutputTokens", "max_output_tokens", "max_tokens"))
    return (None, None)


def classify_metadata_issues(row: Mapping[str, Any], *, provider: Mapping[str, Any] | None = None) -> list[str]:
    """Classify only observable contradictions; modality controls mandatory fields."""
    metadata = row.get("metadata", row)
    provider = provider or {}
    capability_class = str(metadata.get("capabilityClass", "TEXT_GENERATION")).upper()
    if capability_class in {"IMAGE_GENERATION", "AUDIO_GENERATION", "EMBEDDING", "SPECIALIZED_MODALITY"}:
        modality_evidence = metadata.get("modalityEvidence") or metadata.get("capabilityEvidence")
        if metadata.get("modality") and isinstance(modality_evidence, Mapping):
            return []
        return ["MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE"]
    issues: set[str] = set()
    context = _first(metadata, "contextWindow", "context_window", "max_input_tokens", "context")
    output = _first(metadata, "maxOutputTokens", "max_output_tokens", "outputLimit", "max_tokens")
    output_semantics = str(_first(metadata, "outputSemantics", "output_semantics") or "").upper()
    dynamic_output = output_semantics in {"CONTEXT_REMAINDER", "PROVIDER_DYNAMIC", "DYNAMIC"}
    reserved = _first(metadata, "reservedOutputTokens", "reserved_output_tokens", "outputReserve")
    truth_context, truth_output = _truth(metadata)
    if context is None:
        issues.update(("MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE"))
    if output is None and not dynamic_output:
        issues.update(("MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE"))
    if dynamic_output:
        issues.add("OUTPUT_SEMANTICS_DYNAMIC")
    if isinstance(context, (int, float)) and isinstance(output, (int, float)):
        if context == output:
            issues.add("OUTPUT_AS_CONTEXT")
        if context == 262144 and truth_context is None and metadata.get("contextEvidence") in (None, "unknown"):
            issues.add("ACCIDENTAL_262144_FALLBACK")
        attested = _first(metadata, "providerAttestedLimit", "provider_attested_limit")
        if attested is None:
            attested = _first(provider, "providerAttestedLimit", "provider_attested_limit", "contextWindowClamp", "context_window_clamp")
        if isinstance(attested, (int, float)) and context > attested:
            issues.update(("OVERSTATED_CONTEXT", "ROUTER_CLAMP"))
        if isinstance(truth_context, (int, float)) and context > truth_context:
            issues.add("OVERSTATED_CONTEXT")
        if isinstance(truth_output, (int, float)) and output > truth_output:
            issues.add("OUTPUT_LIMIT_MISMATCH")
        if isinstance(reserved, (int, float)) and output + reserved > context:
            issues.add("OUTPUT_LIMIT_MISMATCH")
    if row.get("alias") and row.get("alias") != row.get("resolved"):
        alias_caps = metadata.get("aliasCapabilities", metadata.get("capabilities"))
        resolved_caps = metadata.get("resolvedCapabilities")
        if alias_caps is not None and resolved_caps is not None and alias_caps != resolved_caps:
            issues.update(("ALIAS_CAPABILITY_MISMATCH", "ALIAS_MISMATCH"))
    if row.get("available") is False:
        issues.add("UNAVAILABLE_ROUTE")
    return sorted(issues)


def _status(issues: list[str]) -> str:
    if not issues:
        return "CORRECT"
    if set(issues) == {"OUTPUT_SEMANTICS_DYNAMIC"}:
        return "OUTPUT_SEMANTICS_DYNAMIC"
    for status in ("UNAVAILABLE_ROUTE", "ROUTE_NOT_ADMITTED", "PROVIDER_MODEL_MISSING", "OVERSTATED_CONTEXT", "OUTPUT_AS_CONTEXT", "OUTPUT_LIMIT_MISMATCH",
                   "ROUTER_CLAMP", "ALIAS_MISMATCH", "MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE"):
        if status in issues:
            return status
    return issues[0]


def reconcile_catalog(*, admitted=None, providers=None, routing=None, presets=None,
                      pressure=None, sources=None, provider_metadata=None) -> dict[str, Any]:
    routes = _merge(inventory_routes(admitted=admitted, providers=providers, routing=routing,
                                     presets=presets, pressure=pressure, sources=sources))
    pmeta = _load(provider_metadata) or {}
    admitted_keys = {(row["provider"], row["variant"]) for row in routes if "admitted" in row["categories"]}
    for row in routes:
        if "admitted" in row["categories"] and "providers" not in row["categories"]:
            row.setdefault("catalog_issues", []).append("PROVIDER_MODEL_MISSING")
        if ("runtime" in row["categories"] or "runtime_observed" in row["categories"]) and (row["provider"], row["variant"]) not in admitted_keys:
            row.setdefault("catalog_issues", []).append("ROUTE_NOT_ADMITTED")
    for row in routes:
        provider = dict(pmeta.get(row["provider"], {}) if isinstance(pmeta, Mapping) else {})
        metadata_attested = _first(row["metadata"], "providerAttestedLimit", "provider_attested_limit")
        if metadata_attested is not None and "providerAttestedLimit" not in provider:
            provider["providerAttestedLimit"] = metadata_attested
        row["issues"] = sorted(set(row.get("catalog_issues", [])) | set(classify_metadata_issues(row, provider=provider)))
        limits = effective_limits(row["metadata"], provider)
        truth_context, truth_output = _truth(row["metadata"])
        row.update({
            "current_context": _first(row["metadata"], "contextWindow", "context_window", "max_input_tokens"),
            "current_output": _first(row["metadata"], "maxOutputTokens", "max_output_tokens", "max_tokens"),
            "truth_context": truth_context,
            "truth_output": truth_output,
            "effective_context": limits["context_window"],
            "effective_output": limits["max_output_tokens"],
            "reserved_output": limits["reserved_output_tokens"],
            "provider_attested_limit": limits.get("provider_attested_limit"),
            "status": ("SPECIALIZED_MODALITY" if str(row["metadata"].get("capabilityClass", "")).upper() == "IMAGE_GENERATION" and not row["issues"] else _status(row["issues"])),
            "action": "none" if not (set(row["issues"]) - {"OUTPUT_SEMANTICS_DYNAMIC"}) else "evidence-required",
        })
    families: dict[str, dict[str, Any]] = {}
    for row in routes:
        family = families.setdefault(row["family"], {"family": row["family"], "routes": [],
            "provenance": [], "issues": set(), "contexts": set(), "outputs": set(),
            "current_contexts": set(), "current_outputs": set(), "effective_contexts": set(),
            "effective_outputs": set(), "reserved_outputs": set(), "classes": set(),
            "admitted_routes": [], "unadmitted_routes": []})
        family["routes"].append(f"{row['provider']}:{row['route']}")
        family["provenance"].extend(row["provenance"])
        if "admitted" in row.get("categories", []):
            family["admitted_routes"].append(row)
        else:
            family["unadmitted_routes"].append(row)
        family["classes"].add(str(row["metadata"].get("capabilityClass", "TEXT_GENERATION")).upper())
        if row["truth_context"] is not None:
            family["contexts"].add(row["truth_context"])
        if row["truth_output"] is not None:
            family["outputs"].add(row["truth_output"])
        for field, target in (("current_context", "current_contexts"),
                              ("current_output", "current_outputs"),
                              ("effective_context", "effective_contexts"),
                              ("effective_output", "effective_outputs"),
                              ("reserved_output", "reserved_outputs")):
            if row[field] is not None:
                family[target].add(row[field])
    matrix = []
    for family in sorted(families):
        value = families[family]
        if value["admitted_routes"]:
            # If the family has admitted routes, family capability status is governed by admitted routes
            issues = sorted({issue for r in value["admitted_routes"] for issue in r["issues"]})
        else:
            issues = sorted({issue for r in value["unadmitted_routes"] for issue in r["issues"]})
        matrix.append({"family": family, "routes": sorted(set(value["routes"])),
            "provenance": sorted(set(value["provenance"])), "issues": issues,
            "capability_class": sorted(value["classes"]),
            "current_context": sorted(value["current_contexts"], key=lambda v: (isinstance(v, str), v)),
            "current_output": sorted(value["current_outputs"], key=lambda v: (isinstance(v, str), v)),
            "effective_context": sorted(value["effective_contexts"], key=lambda v: (isinstance(v, str), v)),
            "effective_output": sorted(value["effective_outputs"], key=lambda v: (isinstance(v, str), v)),
            "reserved_output": sorted(value["reserved_outputs"], key=lambda v: (isinstance(v, str), v)),
            "truth_context": sorted(value["contexts"], key=lambda v: (isinstance(v, str), v)),
            "truth_output": sorted(value["outputs"], key=lambda v: (isinstance(v, str), v)),
            "status": ("SPECIALIZED_MODALITY" if value["classes"] == {"IMAGE_GENERATION"} and not issues else _status(issues)),
            "action": "none" if not (set(issues) - {"OUTPUT_SEMANTICS_DYNAMIC"}) else "evidence-required"})
    return {"schema_version": 2, "routes": routes, "families": matrix,
            "issue_vocabulary": list(ISSUES)}


def build_matrix(**kwargs) -> list[dict[str, Any]]:
    return reconcile_catalog(**kwargs)["families"]


def canonical_sources(canonical: Mapping[str, Any], *, runtime_models=None,
                      pressure_routes=None) -> dict[str, list[dict[str, Any]]]:
    """Flatten canonical registries without trusting generated runtime metadata."""
    models = list((canonical.get("models") or {}).get("models") or [])
    by_key = {(m.get("provider"), m.get("id")): m for m in models}
    admitted = []
    for model in models:
        evidence = model.get("capacityEvidence") or {}
        row = dict(model, route=model.get("id"),
                   resolved=evidence.get("upstreamModel", model.get("id")))
        for key in ("contextWindow", "maxOutputTokens"):
            if key not in row and key in evidence:
                row[key] = evidence[key]
        admitted.append(row)
    providers = []
    for provider, definition in ((canonical.get("providers") or {}).get("providers") or {}).items():
        for item in definition.get("models", []) or []:
            model = by_key.get((provider, item.get("id")), {})
            row = {**item, "provider": provider, "route": item.get("id"),
                "resolved": model.get("capacityEvidence", {}).get("upstreamModel", item.get("id"))}
            for key in ("contextWindow", "maxOutputTokens", "outputSemantics", "capabilityClass", "modality", "modalityEvidence", "capacityEvidence"):
                if key in model:
                    row[key] = model[key]
            providers.append(row)
    routing = []
    for name, rule in ((canonical.get("policy") or {}).get("rules") or {}).items():
        if not isinstance(rule, Mapping) or "model" not in rule:
            continue
        model = by_key.get((rule.get("provider"), rule.get("model")), {})
        route = {"provider": rule.get("provider"), "route": name, "model": rule.get("model"),
            "resolved": model.get("capacityEvidence", {}).get("upstreamModel", rule.get("model")),
            "source": rule.get("evidence")}
        evidence = model.get("capacityEvidence") or {}
        for key in ("contextWindow", "maxOutputTokens", "outputSemantics", "capabilityClass", "modality", "modalityEvidence"):
            if key in model:
                route[key] = model[key]
            elif key in evidence:
                route[key] = evidence[key]
        routing.append(route)
    sources = {"admitted": admitted, "providers": providers, "routing": routing}
    if runtime_models is not None:
        sources["runtime"] = list(runtime_models)
    if pressure_routes is not None:
        sources["pressure"] = list(pressure_routes)
    return sources


def runtime_models_from_settings(settings: str | Path, canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read only provider/model ids and non-secret capability fields from DSH settings."""
    data = _load(settings) or {}
    models = list((canonical.get("models") or {}).get("models") or [])
    by_key = {(m.get("provider"), m.get("id")): m for m in models}
    rows = []
    for provider, definition in ((data.get("llm-pi-ai") or {}).get("providers") or {}).items():
        for item in definition.get("models", []) or []:
            model = by_key.get((provider, item.get("id")), {})
            row = {key: item[key] for key in (
                "id", "name", "contextWindow", "maxOutputTokens", "maxTokens",
                "reasoningEfforts", "input", "available") if key in item}
            row.update({"provider": provider, "route": item.get("id"),
                "resolved": model.get("capacityEvidence", {}).get("upstreamModel", item.get("id")),
                "runtime": True})
            rows.append(row)
    return rows


def runtime_models_from_usage(usage: str | Path, canonical: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Project provider/model identities from the redacted usage aggregate only."""
    data = _load(usage) or {}
    models = {(m.get("provider"), m.get("id")): m for m in ((canonical or {}).get("models", {}).get("models", []) or [])}
    rows = []
    for item in data.get("byModel", []) or []:
        key = item.get("key", "")
        if ":" not in key:
            continue
        provider, model = key.split(":", 1)
        evidence = models.get((provider, model), {}).get("capacityEvidence") or {}
        rows.append({"provider": provider, "route": model,
                     "resolved": evidence.get("upstreamModel", model),
                     "runtimeObserved": True, "calls": item.get("calls"),
                     "inputTokens": item.get("input"), "outputTokens": item.get("output")})
    return rows


def pressure_routes_from_harness(contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    composition = ((contract.get("runtime_composition") or {}).get("managed_rows") or {})
    for plugin in composition.get("plugins", []) or []:
        admission = (plugin.get("config") or {}).get("contextAdmission") or {}
        for route in admission.get("routes", []) or []:
            rows.append({**route, "route": route.get("model"), "resolved": route.get("model"),
                         "plugin": plugin.get("id"), "status": "configured"})
    return rows


def write_report(report: Mapping[str, Any], json_path: str | Path, markdown_path: str | Path | None = None) -> None:
    Path(json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown_path is not None:
        lines = ["# DSH model capability reconciliation", "", "Generated/non-canonical evidence report.", "",
                 f"- routes: {len(report.get('routes', []))}", f"- capability families: {len(report.get('families', []))}", "", "## Capability matrix", "",
                 "| Family | Routes | Current context/output | Truth context/output | Effective context/output | Reserved | Status | Action |", "|---|---|---|---|---|---:|---|---|"]
        for row in report.get("families", []):
            lines.append(f"| {row['family']} | {', '.join(row['routes'])} | {row['current_context']}/{row['current_output']} | {row['truth_context']}/{row['truth_output']} | {row['effective_context']}/{row['effective_output']} | {row['reserved_output']} | {row['status']} | {row['action']} |")
        unknown = [f"{row['provider']}/{row['route']} ({row['status']})" for row in report.get("routes", []) if row["status"] != "CORRECT"]
        lines.extend(["", "## Evidence gates", "", "- This is a generated, non-canonical report; no capability value is inferred from DSH metadata alone.",
                      "- Independent capability receipts are required before a non-CORRECT route can enter an overall PASS.",
                      "- Remaining non-CORRECT routes: " + (", ".join(unknown) if unknown else "none") + "."])
        Path(markdown_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# Stable brain-facing aliases.
reconcile = reconcile_catalog
normalize_route_identity = normalize_identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True)
    parser.add_argument("--markdown")
    parser.add_argument("--settings", default=str(Path.home() / ".dsh" / "settings.yaml"))
    parser.add_argument("--usage", default=str(Path(__file__).resolve().parents[2] / "dsh-token-result.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    canonical = {name: _load(root / "registry" / filename) for name, filename in {
        "models": "models.yaml", "providers": "providers.yaml", "policy": "routing-policy.yaml"}.items()}
    sources = canonical_sources(canonical)
    settings_path = Path(args.settings)
    if settings_path.is_file():
        sources["runtime"] = runtime_models_from_settings(settings_path, canonical)
    usage_path = Path(args.usage)
    if usage_path.is_file():
        sources["runtime_observed"] = runtime_models_from_usage(usage_path, canonical)
    contract = _load(root / "registry" / "harnesses" / "dsh.yaml")
    sources["pressure"] = pressure_routes_from_harness(contract)
    report = reconcile_catalog(sources=sources)
    write_report(report, args.json, args.markdown)
    print(json.dumps({"routes": len(report["routes"]), "families": len(report["families"]),
                      "statuses": sorted({r["status"] for r in report["routes"]})}, ensure_ascii=False))
