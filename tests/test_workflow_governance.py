#!/usr/bin/env python3
"""
Unit and Regression Test Suite for Workflow Subagent Governance (Scenarios A through M)
"""

import unittest
import sys
import os
import importlib.util

# 鍔ㄦ€佸姞杞藉甫鐭í绾胯矾寰勭殑妯″潡
script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "subagent-execution-governance", "scripts", "workflow_preflight_router.py"))
spec = importlib.util.spec_from_file_location("workflow_preflight_router", script_path)
router_mod = importlib.util.module_from_spec(spec)
sys.modules["workflow_preflight_router"] = router_mod
spec.loader.exec_module(router_mod)

WorkflowPreflightRouter = router_mod.WorkflowPreflightRouter
ModelInventory = router_mod.ModelInventory
LOGICAL_ROLES = router_mod.LOGICAL_ROLES
EXPLICIT_FALLBACK_RULES = router_mod.EXPLICIT_FALLBACK_RULES


class TestWorkflowGovernanceScenarios(unittest.TestCase):

    def setUp(self):
        # 鏋勯€犳ā鎷熺殑 DSH 鍑嗗叆妯″瀷閰嶇疆锛堜笌鐢熶骇鐪熷疄 settings.yaml 涓€鑷达級
        self.custom_dsh_models = {
            "cpa": [
                "gemini-3.7-flash-high",
                "claude-sonnet-4-6",
                "gpt-image-2",
                "claude-opus-4-6-thinking",
                "gpt-5.6-luna-max",
                "gpt-5.6-sol-xhigh"
            ]
        }
        self.inventory = ModelInventory(custom_dsh_models=self.custom_dsh_models)
        self.router = WorkflowPreflightRouter(self.inventory)

    # A. 宸插噯鍏ユā鍨嬫甯告墽琛?    def test_scenario_A_admitted_model_success(self):
        res = self.router.resolve_target("gemini-3.7-flash-high", "cpa")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["resolved_model"], "gemini-3.7-flash-high")
        self.assertEqual(res["resolved_provider"], "cpa")
        self.assertFalse(res["fallback_applied"])
        self.assertEqual(res["provenance"], "DECLARED_EXACT")

    # B. gateway 瀛樺湪浣?DSH 鏈噯鍏ョ殑妯″瀷 (濡?gpt-5.6-luna)
    def test_scenario_B_gateway_exists_dsh_unadmitted_with_fallback(self):
        res = self.router.resolve_target("gpt-5.6-luna", "cpa")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["resolved_model"], "gpt-5.6-luna-max")
        self.assertTrue(res["fallback_applied"])
        self.assertEqual(res["fallback_details"]["reason_code"], "GATEWAY_ADMITTED_ALIAS_UPGRADE")
        self.assertEqual(res["provenance"], "EXPLICIT_POLICY_FALLBACK")

    # C. 瀹屽叏涓嶅瓨鍦ㄧ殑妯″瀷
    def test_scenario_C_nonexistent_model_fails_closed(self):
        res = self.router.resolve_target("gpt-9.9-hyper-nonexistent", "cpa")
        self.assertEqual(res["status"], "unresolved")
        self.assertEqual(res["error_code"], "UNADMITTED_OR_UNKNOWN_MODEL")
        self.assertFalse(res["fallback_applied"])

    # D. 缂哄皯 provider / 鏈煡 provider 鐨勬ā鍨?    def test_scenario_D_unknown_provider_fails_closed(self):
        res = self.router.resolve_target("gemini-3.7-flash-high", "unknown_provider")
        self.assertEqual(res["status"], "unresolved")
        self.assertEqual(res["error_code"], "UNADMITTED_OR_UNKNOWN_MODEL")

    # E. Provider 涓存椂 5xx / 缁撴瀯鍖栧瓙浠ｇ悊鎶ラ敊澶勭悊
    def test_scenario_E_structured_child_error_reporting(self):
        error_result = {
            "status": "error",
            "requested_model": "gemini-3.7-flash-high",
            "resolved_model": "gemini-3.7-flash-high",
            "error_code": "PROVIDER_HTTP_500",
            "error_message": "Internal Server Error from upstream gateway"
        }
        agg = self.router.aggregate_and_validate_results([error_result], expected_count=1)
        self.assertEqual(agg["verdict"], "ALL_FAILED")
        self.assertEqual(len(agg["failed_items"]), 1)
        self.assertEqual(agg["failed_items"][0]["code"], "PROVIDER_HTTP_500")

    # F. 鍗曚釜瀛愪唬鐞嗗け璐ャ€佸叾浠栨垚鍔?(PARTIAL_SUCCESS)
    def test_scenario_F_partial_success(self):
        results = [
            {"status": "success", "cluster_id": "c1", "content": "ok"},
            {"status": "error", "error_code": "RATE_LIMIT", "error_message": "429 Too Many Requests"},
            {"status": "success", "cluster_id": "c3", "content": "ok"}
        ]
        agg = self.router.aggregate_and_validate_results(results, expected_count=3, required_keys=["cluster_id"])
        self.assertEqual(agg["verdict"], "PARTIAL_SUCCESS")
        self.assertEqual(agg["success_count"], 2)
        self.assertEqual(agg["failed_count"], 1)

    # G. 鍏ㄩ儴瀛愪唬鐞嗗け璐?(ALL_FAILED 骞剁粰鍑鸿瘖鏂?
    def test_scenario_G_all_failed_triggers_diagnostics(self):
        results = [None, None, None]
        agg = self.router.aggregate_and_validate_results(results, expected_count=3)
        self.assertEqual(agg["verdict"], "ALL_FAILED")
        self.assertEqual(agg["success_count"], 0)
        self.assertIn("self_healing_action", agg["diagnostics"])

    # H. agent 杩斿洖 null 鏃惰瘑鍒负澶辫触椤?    def test_scenario_H_null_returns_detected_as_failure(self):
        results = [
            {"cluster_id": "c1", "text": "done"},
            None
        ]
        agg = self.router.aggregate_and_validate_results(results, expected_count=2)
        self.assertEqual(agg["verdict"], "PARTIAL_SUCCESS")
        self.assertEqual(agg["failed_count"], 1)
        self.assertIn("null", agg["failed_items"][0]["error"])

    # I. 杈撳嚭 schema 涓嶅畬鏁?/ 缂哄皯蹇呭～瀛楁
    def test_scenario_I_schema_incomplete_detected(self):
        results = [
            {"cluster_id": "c1", "repair_mechanism": "fix"},
            {"cluster_id": "c2"}  # 缂哄皯 repair_mechanism
        ]
        agg = self.router.aggregate_and_validate_results(results, expected_count=2, required_keys=["cluster_id", "repair_mechanism"])
        self.assertEqual(agg["verdict"], "PARTIAL_SUCCESS")
        self.assertEqual(agg["success_count"], 1)
        self.assertEqual(agg["failed_count"], 1)
        self.assertIn("Missing required keys", agg["failed_items"][0]["error"])

    # J. policy 澹版槑 fallback 鏃惰褰曞畬鏁?audit 淇℃伅
    def test_scenario_J_policy_declared_fallback_audit(self):
        res = self.router.resolve_target("gpt-5.6-luna", "cpa", allow_fallback=True)
        self.assertTrue(res["fallback_applied"])
        details = res["fallback_details"]
        self.assertEqual(details["original_requested"], "gpt-5.6-luna")
        self.assertEqual(details["fallback_model"], "gpt-5.6-luna-max")
        self.assertEqual(details["reason_code"], "GATEWAY_ADMITTED_ALIAS_UPGRADE")
        # 璇箟璇氬疄鍖栵紙2026-08-31 楠屾敹锛夛細浠呮湁鍚屼竴涓婃父鍩哄骇璇佹嵁锛屾。浣嶅奖鍝嶆湭鐭?        self.assertEqual(details["quality_tier_impact"], "UNKNOWN")
        self.assertEqual(details["mapping_type"], "EXPLICIT_COMPATIBILITY_MAPPING")

    # K. policy 鏈０鏄?fallback 鏃跺繀椤?fail closed
    def test_scenario_K_policy_undeclared_fallback_fails_closed(self):
        # 鍏抽棴 fallback 鎴栬姹傛棤 fallback 瑙勫垯鐨勬ā鍨?        res = self.router.resolve_target("unmapped-model-x", "cpa", allow_fallback=True)
        self.assertEqual(res["status"], "unresolved")

        # 鍗充娇鏄?luna锛屽鏋滄樉寮忕姝?fallback 涔熷繀椤?fail closed
        res_no_fb = self.router.resolve_target("gpt-5.6-luna", "cpa", allow_fallback=False)
        self.assertEqual(res_no_fb["status"], "unresolved")

    # L. fallback 鍚?requested/resolved/provider-reported 韬唤閾惧畬鏁?    def test_scenario_L_trace_identity_chain(self):
        res = self.router.resolve_target("gpt-5.6-luna", "cpa")
        trace_record = {
            "requested_model": res["requested"],
            "resolved_model": res["resolved_model"],
            "resolved_provider": res["resolved_provider"],
            "fallback_applied": res["fallback_applied"],
            "reason_code": res["fallback_details"]["reason_code"]
        }
        self.assertEqual(trace_record["requested_model"], "gpt-5.6-luna")
        self.assertEqual(trace_record["resolved_model"], "gpt-5.6-luna-max")
        self.assertEqual(trace_record["resolved_provider"], "cpa")

    # M. 鐪熷疄澶氬瓙浠ｇ悊 workflow 娴佺▼锛歅re-flight 楠岃瘉 + 閫昏緫瑙掕壊瑙ｆ瀽锛屽叏绋嬫棤闇€鐢ㄦ埛浠嬪叆
    def test_scenario_M_end_to_end_workflow_preflight_and_execution(self):
        # 瀹氫箟鍖呭惈 10 涓瓙浠诲姟鐨?workflow 瑙勮寖锛堜娇鐢ㄩ€昏緫瑙掕壊锛岄伩鍏嶇‖缂栫爜骞昏锛?        workflow_spec = [
            {"label": f"cluster-{i+1}", "phase": "Cluster designs", "model": "cheap_executor"}
            for i in range(6)
        ] + [
            {"label": "method-review", "phase": "Conflict review", "model": "strong_reviewer"},
            {"label": "topology-review", "phase": "Conflict review", "model": "strong_reviewer"},
            {"label": "construct-review", "phase": "Conflict review", "model": "strong_reviewer"},
            {"label": "lead-contract", "phase": "Unified repair contract", "model": "frontier_architect"}
        ]

        # 1. 杩愯 pre-flight
        preflight = self.router.validate_workflow_spec(workflow_spec)
        self.assertTrue(preflight["valid"])
        self.assertEqual(preflight["error_count"], 0)
        self.assertEqual(len(preflight["resolved_agents"]), 10)

        # 妫€鏌ラ€昏緫瑙掕壊鏄惁琚噯纭В鏋愪负鍑嗗叆妯″瀷
        first_agent = preflight["resolved_agents"][0]
        self.assertEqual(first_agent["resolution"]["resolved_model"], "gpt-5.6-luna-max")
        self.assertEqual(first_agent["resolution"]["resolved_provider"], "cpa")

        lead_agent = preflight["resolved_agents"][9]
        self.assertEqual(lead_agent["resolution"]["resolved_model"], "gpt-5.6-sol-xhigh")

        # 2. 妯℃嫙鎵ц缁撴灉鑱氬悎
        mock_results = [
            {"cluster_id": f"c{i+1}", "status": "success", "data": "ok"}
            for i in range(10)
        ]
        agg = self.router.aggregate_and_validate_results(mock_results, expected_count=10, required_keys=["cluster_id"])
        self.assertEqual(agg["verdict"], "SUCCESS")
        self.assertEqual(agg["success_count"], 10)
        self.assertEqual(agg["failed_count"], 0)


if __name__ == "__main__":
    unittest.main()
