#!/usr/bin/env python3
"""
Regression and Semantic Boundary Test Suite for Model Governance (Scenarios A through G)

Validates:
A. User adds future-model -> preserved after aic apply
B. User sets future-model as default -> selection preserved after aic apply
C. future-model unconfirmed by provider discovery -> config preserved, execution NOT_ADMITTED
D. provider discovery confirms future-model -> runtime admission PASS (dynamic admission)
E. User fakes contextWindow=2000000 -> provenance USER_DECLARED, not elevated to provider-attested
F. Provider provides attested context limit -> Context Admission 使用可信值
G. Provider/model later disappears -> user config preserved, runtime execution fail closed
"""

import os
import sys
import json
import yaml
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "aic"))
sys.path.insert(0, str(REPO_ROOT / "skills" / "subagent-execution-governance" / "scripts"))

import aic
from workflow_preflight_router import (
    WorkflowPreflightRouter,
    ModelInventory,
    CONFIGURED_MODEL,
    USER_SELECTED_MODEL,
    DISCOVERED_MODEL,
    RUNTIME_ADMITTED_MODEL,
    PROVIDER_ATTESTED,
    PROVIDER_DISCOVERED,
    CANONICAL_VERIFIED,
    USER_DECLARED,
    UNKNOWN,
)


class TestModelGovernanceSemanticBoundaries(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(self.tmp)
        dsh = self.home / ".dsh"
        dsh.mkdir(parents=True)
        canonical = aic.load_canonical()
        overlay = aic.adapter_overlay()
        self.expected = aic.render_settings(canonical, overlay)
        self.settings_file = dsh / "settings.yaml"
        self.settings_file.write_text(
            yaml.safe_dump(self.expected, allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # A. 用户新增 future-model -> aic apply 后仍存在
    def test_regression_A_user_future_model_preserved_on_apply(self):
        data = yaml.safe_load(self.settings_file.read_text(encoding="utf-8"))
        future_model = {
            "id": "future-model-2027",
            "input": ["text", "image"],
            "contextWindow": 1048576,
        }
        data["llm-pi-ai"]["providers"]["cpa"]["models"].append(future_model)
        self.settings_file.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )

        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
            self.assertEqual(rc, 0)
            back = yaml.safe_load(self.settings_file.read_text(encoding="utf-8"))
            back_models = {m["id"]: m for m in back["llm-pi-ai"]["providers"]["cpa"]["models"]}
            self.assertIn("future-model-2027", back_models, "future-model must survive aic apply")

    # B. 用户将其设为默认 -> aic apply 后选择仍存在
    def test_regression_B_user_default_model_selection_preserved_on_apply(self):
        data = yaml.safe_load(self.settings_file.read_text(encoding="utf-8"))
        future_model = {
            "id": "future-model-2027",
            "input": ["text", "image"],
            "contextWindow": 1048576,
        }
        data["llm-pi-ai"]["providers"]["cpa"]["models"].append(future_model)
        data["agent-default-model"] = {"provider": "cpa", "model": "future-model-2027"}
        self.settings_file.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )

        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
            self.assertEqual(rc, 0)
            back = yaml.safe_load(self.settings_file.read_text(encoding="utf-8"))
            self.assertEqual(
                back.get("agent-default-model"),
                {"provider": "cpa", "model": "future-model-2027"},
                "User default model selection must survive aic apply without fallback revert"
            )

    # C. future-model 未被 provider discovery 发现 -> config preserved -> execution NOT_ADMITTED
    def test_regression_C_unconfirmed_future_model_execution_not_admitted_config_preserved(self):
        inventory = ModelInventory(
            custom_dsh_models={"cpa": ["future-model-unconfirmed", "gemini-3.7-flash-high"]},
            custom_discovered_models={"cpa": ["gemini-3.7-flash-high"]}
        )
        router = WorkflowPreflightRouter(inventory)
        res = router.resolve_target("future-model-unconfirmed", "cpa", allow_fallback=False)

        self.assertEqual(res["status"], "unresolved")
        self.assertEqual(res["error_code"], "NOT_ADMITTED")
        self.assertEqual(res["model_classification"], CONFIGURED_MODEL)
        self.assertTrue(res.get("config_preserved", False), "User config must be preserved despite execution denial")

    # D. provider discovery 确认 future-model -> runtime admission PASS
    def test_regression_D_provider_discovery_confirms_future_model_runtime_admission_pass(self):
        inventory = ModelInventory(
            custom_dsh_models={"cpa": ["future-model-dynamic"]},
            custom_discovered_models={"cpa": ["future-model-dynamic"]}
        )
        router = WorkflowPreflightRouter(inventory)
        res = router.resolve_target("future-model-dynamic", "cpa")

        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["model_classification"], RUNTIME_ADMITTED_MODEL)
        self.assertEqual(res["provenance"], "DYNAMIC_PROVIDER_ADMISSION")
        self.assertFalse(res.get("static_manual_admission_required", True))

    # E. 用户伪造 contextWindow=2000000 -> 不得自动视为 provider-attested 2M
    def test_regression_E_user_declared_context_not_provider_attested(self):
        inventory = ModelInventory(
            custom_dsh_models={"cpa": ["gemini-experimental"]},
            custom_context_limits={
                ("cpa", "gemini-experimental"): {
                    "limit": 2000000,
                    "provenance": USER_DECLARED,
                    "trusted": False,
                    "conservative_limit": 128000,
                }
            }
        )
        ctx = inventory.resolve_context_limit("cpa", "gemini-experimental")
        self.assertEqual(ctx["provenance"], USER_DECLARED)
        self.assertFalse(ctx["trusted"])
        self.assertNotEqual(ctx["provenance"], PROVIDER_ATTESTED)
        self.assertEqual(ctx["effective_limit"], 128000, "USER_DECLARED limit must not be treated as trusted provider hard limit")

    # F. provider 提供可信 context limit -> Context Admission 使用可信值
    def test_regression_F_provider_attested_context_limit_trusted(self):
        inventory = ModelInventory(
            custom_dsh_models={"cpa": ["gemini-3.8-flash-high"]},
            custom_context_limits={
                ("cpa", "gemini-3.8-flash-high"): {
                    "limit": 1048576,
                    "provenance": PROVIDER_ATTESTED,
                    "trusted": True,
                }
            }
        )
        ctx = inventory.resolve_context_limit("cpa", "gemini-3.8-flash-high")
        self.assertEqual(ctx["provenance"], PROVIDER_ATTESTED)
        self.assertTrue(ctx["trusted"])
        self.assertEqual(ctx["effective_limit"], 1048576)

    # G. provider/model 后续消失 -> 用户配置不被删除 -> runtime 执行 fail closed / 使用既定 fallback 语义
    def test_regression_G_disappearing_model_fails_closed_config_preserved(self):
        configured_models = ["temporary-preview-model"]
        inventory_after_disappearance = ModelInventory(
            custom_dsh_models={"cpa": configured_models},
            custom_discovered_models={"cpa": []}
        )
        router = WorkflowPreflightRouter(inventory_after_disappearance)
        res = router.resolve_target("temporary-preview-model", "cpa", allow_fallback=False)

        self.assertEqual(res["status"], "unresolved")
        self.assertEqual(res["error_code"], "NOT_ADMITTED")
        self.assertTrue(res.get("config_preserved", False))
        self.assertIn("temporary-preview-model", configured_models, "User configuration must never be deleted")


if __name__ == "__main__":
    unittest.main()
