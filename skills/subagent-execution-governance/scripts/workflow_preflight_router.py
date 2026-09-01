#!/usr/bin/env python3
"""
Workflow Pre-flight Router & Governance Engine

Provides:
1. Logical Role Resolution (subagent_default, cheap_executor, strong_reviewer, frontier_architect)
2. Four-layer Model Verification (Gateway Inventory, AIC Catalog, DSH Settings, Workflow Runtime)
3. Explicit, Non-Silent Fallback Policy with Trace Identity Preservation
4. Pre-flight Validation before Spawning Concurrent Subagents
5. Structured Execution Aggregation & Failure Self-Healing Diagnoser
"""

import os
import json
import yaml
from typing import Dict, Any, List, Optional, Tuple

DEFAULT_SETTINGS_PATH = os.path.expanduser("~/.dsh/settings.yaml")

# 1. 閫昏緫瑙掕壊榛樿鏄犲皠琛?(SSOT)
LOGICAL_ROLES = {
    "subagent_default": {
        "provider": "cpa",
        "model": "gemini-3.7-flash-high",
        "tier": "standard_workhorse",
        "description": "Default workhorse for implementation and balanced tasks"
    },
    "cheap_executor": {
        "provider": "cpa",
        "model": "gpt-5.6-luna-max",
        "tier": "fast_cheap",
        "description": "Fast and economical executor for bounded discovery/extraction"
    },
    "strong_reviewer": {
        "provider": "cpa",
        "model": "claude-sonnet-4-6",
        "tier": "high_reasoning",
        "description": "High reasoning review for adversarial check and conflict analysis"
    },
    "frontier_architect": {
        "provider": "cpa",
        "model": "gpt-5.6-sol-xhigh",
        "tier": "frontier",
        "description": "Frontier model for architecture and unified contract synthesis"
    }
}

# 2. 鏄惧紡 Fallback 绛栫暐瑙勫垯琛?(蹇呴』鏄惧紡澹版槑锛岀姝㈤殣寮?闈欓粯鏇挎崲)
EXPLICIT_FALLBACK_RULES = {
    "gpt-5.6-luna": {
        "fallback_model": "gpt-5.6-luna-max",
        "fallback_provider": "cpa",
        "reason_code": "GATEWAY_ADMITTED_ALIAS_UPGRADE",
        "policy_rule": "Map bare luna to registered luna-max in cpa provider",
        # 瀹炴祴璇佹嵁锛氳姹?gpt-5.6-luna-max 鏃?provider responseModel=gpt-5.6-luna锛?        # 璇佹槑浜岃€呮寚鍚戝悓涓€涓婃父鍩哄骇妯″瀷锛涗絾 "-max" 鏄惁浠呬负鎺ㄧ悊妗ｄ綅鍚庣紑鏈瘉瀹烇紝
        # 鍥犳涓嶅绉?NEUTRAL锛屾寜鏄惧紡鍏煎鏄犲皠澶勭悊銆?        "mapping_type": "EXPLICIT_COMPATIBILITY_MAPPING",
        "quality_tier_impact": "UNKNOWN"
    },
    "gpt-5.6-sol": {
        "fallback_model": "gpt-5.6-sol-xhigh",
        "fallback_provider": "cpa",
        "reason_code": "GATEWAY_ADMITTED_ALIAS_UPGRADE",
        "policy_rule": "Map bare sol to registered sol-xhigh in cpa provider",
        "quality_tier_impact": "NEUTRAL"
    }
}


class ModelInventory:
    """Represents the multi-layer model catalog facts."""
    def __init__(self, dsh_settings_path: str = DEFAULT_SETTINGS_PATH, custom_dsh_models: Optional[Dict[str, List[str]]] = None):
        self.dsh_models: Dict[str, List[str]] = {}
        if custom_dsh_models is not None:
            self.dsh_models = custom_dsh_models
        else:
            self._load_dsh_settings(dsh_settings_path)

    def _load_dsh_settings(self, path: str):
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            providers = data.get("llm-pi-ai", {}).get("providers", {})
            for prov_name, prov_conf in providers.items():
                if not isinstance(prov_conf, dict):
                    continue
                models_list = prov_conf.get("models", [])
                self.dsh_models[prov_name] = [
                    m.get("id") for m in models_list if isinstance(m, dict) and "id" in m
                ]
        except Exception:
            pass


class WorkflowPreflightRouter:
    """Pre-flight router and aggregator for workflow child agents."""

    def __init__(self, inventory: Optional[ModelInventory] = None):
        self.inventory = inventory or ModelInventory()

    def resolve_target(self, role_or_model: str, explicit_provider: Optional[str] = None, allow_fallback: bool = True) -> Dict[str, Any]:
        """
        Resolves a logical role or model ID to an exact { provider, model } tuple.
        Returns a rich resolution descriptor with provenance and fallback tracking.
        """
        requested = role_or_model.strip()

        # 1. 妫€鏌ユ槸鍚︿负閫昏緫瑙掕壊
        if requested in LOGICAL_ROLES:
            role_def = LOGICAL_ROLES[requested]
            target_prov = explicit_provider or role_def["provider"]
            target_mod = role_def["model"]

            declared = self.inventory.dsh_models.get(target_prov, [])
            if target_mod in declared:
                return {
                    "status": "resolved",
                    "requested": requested,
                    "is_logical_role": True,
                    "resolved_provider": target_prov,
                    "resolved_model": target_mod,
                    "tier": role_def["tier"],
                    "fallback_applied": False,
                    "provenance": "LOGICAL_ROLE_DIRECT"
                }

        # 2. 妫€鏌ュ叿浣撴ā鍨?ID
        provider = explicit_provider or "cpa"
        declared_models = self.inventory.dsh_models.get(provider, [])

        # 2.1 鐩存帴鍛戒腑 DSH 鍑嗗叆澹版槑
        if requested in declared_models:
            return {
                "status": "resolved",
                "requested": requested,
                "is_logical_role": False,
                "resolved_provider": provider,
                "resolved_model": requested,
                "fallback_applied": False,
                "provenance": "DECLARED_EXACT"
            }

        # 2.2 鏈洿鎺ュ懡涓紝妫€鏌ユ樉寮?Fallback 绛栫暐瑙勫垯
        if allow_fallback and requested in EXPLICIT_FALLBACK_RULES:
            rule = EXPLICIT_FALLBACK_RULES[requested]
            fb_model = rule["fallback_model"]
            fb_prov = rule["fallback_provider"]

            if fb_model in self.inventory.dsh_models.get(fb_prov, []):
                return {
                    "status": "resolved",
                    "requested": requested,
                    "is_logical_role": False,
                    "resolved_provider": fb_prov,
                    "resolved_model": fb_model,
                    "fallback_applied": True,
                    "fallback_details": {
                        "original_requested": requested,
                        "fallback_model": fb_model,
                        "reason_code": rule["reason_code"],
                        "policy_rule": rule["policy_rule"],
                        "mapping_type": rule.get("mapping_type", "EXPLICIT_COMPATIBILITY_MAPPING"),
                        "quality_tier_impact": rule["quality_tier_impact"]
                    },
                    "provenance": "EXPLICIT_POLICY_FALLBACK"
                }

        # 2.3 鏃犲彲鐢?fallback锛屽繀椤?FAIL CLOSED
        return {
            "status": "unresolved",
            "requested": requested,
            "provider": provider,
            "error_code": "UNADMITTED_OR_UNKNOWN_MODEL",
            "error_message": f"Model '{requested}' is not declared in DSH settings for provider '{provider}', and no explicit fallback policy permits its conversion.",
            "fallback_applied": False
        }

    def validate_workflow_spec(self, agents_spec: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs pre-flight verification on a batch of agent specifications before execution.
        Fails fast before any worker thread or child process is spawned.
        """
        errors = []
        resolved_agents = []

        for idx, spec in enumerate(agents_spec):
            label = spec.get("label", f"agent-{idx+1}")
            req_model = spec.get("model") or "subagent_default"
            req_prov = spec.get("provider")

            res = self.resolve_target(req_model, req_prov)
            if res["status"] != "resolved":
                errors.append({
                    "index": idx,
                    "label": label,
                    "error_code": res["error_code"],
                    "error_message": res["error_message"]
                })
            else:
                resolved_agents.append({
                    "index": idx,
                    "label": label,
                    "phase": spec.get("phase"),
                    "prompt": spec.get("prompt"),
                    "schema": spec.get("schema"),
                    "resolution": res
                })

        if errors:
            return {
                "valid": False,
                "error_count": len(errors),
                "errors": errors,
                "resolved_agents": []
            }

        return {
            "valid": True,
            "error_count": 0,
            "errors": [],
            "resolved_agents": resolved_agents
        }

    def aggregate_and_validate_results(
        self,
        results: List[Any],
        expected_count: int,
        required_keys: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Validates aggregated workflow results:
        1. Count matches expected
        2. At least one success (no all-null or all-failed)
        3. Required schema keys exist and non-null
        4. Structured error diagnostics when failures occur
        """
        if len(results) != expected_count:
            return {
                "verdict": "FAIL",
                "reason": f"Expected {expected_count} results, but received {len(results)}."
            }

        valid_items = []
        failed_items = []

        for idx, item in enumerate(results):
            if item is None:
                failed_items.append({
                    "index": idx,
                    "error": "Item returned null (child execution failed or threw uncaught exception)"
                })
            elif isinstance(item, dict) and item.get("status") in ("error", "failed", "timeout"):
                failed_items.append({
                    "index": idx,
                    "error": item.get("error_message", "Structured failure reported"),
                    "code": item.get("error_code", "UNKNOWN_CHILD_ERROR")
                })
            elif isinstance(item, dict):
                # 鏍￠獙蹇呭～瀛楁
                if required_keys:
                    missing = [k for k in required_keys if k not in item or item[k] is None]
                    if missing:
                        failed_items.append({
                            "index": idx,
                            "error": f"Missing required keys: {missing}"
                        })
                        continue
                valid_items.append(item)
            else:
                valid_items.append(item)

        if len(valid_items) == 0:
            return {
                "verdict": "ALL_FAILED",
                "success_count": 0,
                "failed_count": len(failed_items),
                "failed_items": failed_items,
                "diagnostics": {
                    "self_healing_action": "REFRESH_CATALOG_AND_RE_RESOLVE_LOGICAL_ROLES",
                    "details": "All workflow members failed. Likely pre-flight or routing mismatch."
                }
            }

        return {
            "verdict": "SUCCESS" if len(failed_items) == 0 else "PARTIAL_SUCCESS",
            "success_count": len(valid_items),
            "failed_count": len(failed_items),
            "valid_items": valid_items,
            "failed_items": failed_items
        }
