import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "aic"))

import model_capability_reconciliation as mcr  # noqa: E402


class TestModelCapabilityReconciliation(unittest.TestCase):
    def test_01_identity_is_provider_qualified(self):
        ident = mcr.normalize_identity({"provider": "p", "route": "r", "variant": "v", "resolved": "u"})
        self.assertEqual((ident["provider"], ident["route"], ident["variant"], ident["resolved"]), ("p", "r", "v", "u"))

    def test_02_family_dedupes_aliases_by_resolved_identity(self):
        rows = mcr.inventory_routes(sources={"routing": [
            {"provider": "p1", "route": "a", "variant": "alias", "resolved": "model"},
            {"provider": "p2", "route": "b", "variant": "other", "resolved": "model"},
        ]})
        report = mcr.reconcile_catalog(sources={"routing": rows})
        self.assertEqual(len(report["families"]), 2)

    def test_03_provenance_is_preserved_and_sorted(self):
        report = mcr.reconcile_catalog(sources={"routing": {"z": {"provider": "p", "contextWindow": 10, "maxOutputTokens": 2}}})
        self.assertEqual(report["routes"][0]["provenance"], ["routing.z"])

    def test_04_missing_metadata_requires_evidence(self):
        issues = mcr.classify_metadata_issues({"metadata": {}, "available": True})
        self.assertEqual(issues, ["MISSING_METADATA", "UNKNOWN_NEEDS_EVIDENCE"])

    def test_05_output_cannot_be_used_as_context(self):
        issues = mcr.classify_metadata_issues({"metadata": {"contextWindow": 100, "maxOutputTokens": 100}})
        self.assertIn("OUTPUT_AS_CONTEXT", issues)

    def test_06_accidental_262144_fallback_is_visible(self):
        issues = mcr.classify_metadata_issues({"metadata": {"contextWindow": 262144, "maxOutputTokens": 100}})
        self.assertIn("ACCIDENTAL_262144_FALLBACK", issues)

    def test_07_alias_capability_mismatch_is_visible(self):
        issues = mcr.classify_metadata_issues({"alias": "a", "resolved": "b", "metadata": {
            "contextWindow": 100, "maxOutputTokens": 10,
            "aliasCapabilities": ["vision"], "resolvedCapabilities": ["text"]}})
        self.assertIn("ALIAS_CAPABILITY_MISMATCH", issues)

    def test_08_context_evidence_and_output_limit_mismatch(self):
        issues = mcr.classify_metadata_issues({"metadata": {
            "contextWindow": 200, "maxOutputTokens": 90,
            "capacityEvidence": {"contextWindow": 100, "maxOutputTokens": 50}}})
        self.assertIn("OVERSTATED_CONTEXT", issues)
        self.assertIn("OUTPUT_LIMIT_MISMATCH", issues)

    def test_09_provider_clamp_min_and_reserved_output(self):
        limits = mcr.effective_limits({"contextWindow": 100, "maxOutputTokens": 80}, {
            "contextWindowClampMin": 128, "reservedOutputTokens": 16})
        self.assertEqual(limits, {"context_window": 128, "max_output_tokens": 80,
                                  "reserved_output_tokens": 16, "usable_input_tokens": 112})

    def test_10_unavailable_route_and_deterministic_matrix(self):
        kwargs = {"sources": {"pressure": [
            {"provider": "p", "route": "r", "contextWindow": 100, "maxOutputTokens": 10, "available": False},
        ]}}
        first = mcr.reconcile_catalog(**kwargs)
        second = mcr.reconcile_catalog(**kwargs)
        self.assertEqual(first, second)
        self.assertIn("UNAVAILABLE_ROUTE", first["routes"][0]["issues"])
        self.assertEqual(mcr.build_matrix(**kwargs), first["families"])
    def test_11_canonical_inventory_covers_admitted_routes_and_provenance(self):
        canonical = {
            "models": mcr._load(ROOT / "registry" / "models.yaml"),
            "providers": mcr._load(ROOT / "registry" / "providers.yaml"),
            "policy": mcr._load(ROOT / "registry" / "routing-policy.yaml"),
        }
        sources = mcr.canonical_sources(canonical)
        report = mcr.reconcile_catalog(sources=sources)
        admitted = [row for row in report["routes"] if "admitted" in row["categories"]]
        self.assertEqual(len(admitted), 10)
        self.assertTrue(all(row["provenance"] for row in admitted))
        self.assertIn("kimi-coding:k3-256k", {route for family in report["families"] for route in family["routes"]})

    def test_12_provider_attested_limit_is_an_upper_clamp(self):
        limits = mcr.effective_limits({"contextWindow": 1000, "maxOutputTokens": 200}, {
            "providerAttestedLimit": 800, "reservedOutputTokens": 100})
        self.assertEqual(limits["context_window"], 800)
        self.assertEqual(limits["usable_input_tokens"], 700)
        self.assertEqual(limits["max_output_tokens"], 200)

    def test_13_unadmitted_runtime_route_is_explicit(self):
        report = mcr.reconcile_catalog(sources={"admitted": [
            {"provider": "p", "id": "known", "resolved": "known", "contextWindow": 100, "maxOutputTokens": 10}
        ], "runtime": [
            {"provider": "p", "id": "unknown", "resolved": "unknown", "contextWindow": 100, "maxOutputTokens": 10}
        ]})
        unknown = next(row for row in report["routes"] if row["route"] == "unknown")
    def test_14_specialized_image_route_does_not_require_text_limits(self):
        report = mcr.reconcile_catalog(sources={"admitted": [{
            "provider": "cpa", "id": "gpt-image-2", "capabilityClass": "IMAGE_GENERATION",
            "modality": "image_generation", "modalityEvidence": {
                "grade": "authoritative", "input": ["text", "image"], "output": ["image"]
            }
        }], "providers": [{
            "provider": "cpa", "id": "gpt-image-2", "capabilityClass": "IMAGE_GENERATION",
            "modality": "image_generation", "modalityEvidence": {"grade": "authoritative"}
        }]})
        row = report["routes"][0]
        self.assertEqual(row["status"], "SPECIALIZED_MODALITY")
        self.assertEqual(row["issues"], [])

    def test_15_dynamic_output_semantics_is_not_missing_metadata(self):
        issues = mcr.classify_metadata_issues({"metadata": {
            "contextWindow": 262144, "outputSemantics": "CONTEXT_REMAINDER",
            "capacityEvidence": {"contextWindow": 262144, "outputSemantics": "CONTEXT_REMAINDER"}
        }})
        self.assertEqual(issues, ["OUTPUT_SEMANTICS_DYNAMIC"])

    def test_16_mixed_admission_family_preserves_route_admission_and_family_status(self):
        report = mcr.reconcile_catalog(sources={
            "admitted": [{
                "provider": "cpa", "id": "gpt-5.6-sol-xhigh", "resolved": "gpt-5.6-sol",
                "contextWindow": 1050000, "maxOutputTokens": 128000, "capabilityClass": "TEXT_GENERATION",
            }],
            "providers": [{
                "provider": "cpa", "id": "gpt-5.6-sol-xhigh", "resolved": "gpt-5.6-sol",
                "contextWindow": 1050000, "maxOutputTokens": 128000, "capabilityClass": "TEXT_GENERATION",
            }],
            "runtime_observed": [{
                "provider": "cpa", "route": "gpt-5.6-sol", "resolved": "gpt-5.6-sol",
                "calls": 10,
            }],
        })
        routes_by_name = {r["route"]: r for r in report["routes"]}
        self.assertEqual(routes_by_name["gpt-5.6-sol-xhigh"]["status"], "CORRECT")
        self.assertEqual(routes_by_name["gpt-5.6-sol"]["status"], "ROUTE_NOT_ADMITTED")
        family = next(f for f in report["families"] if f["family"] == "cpa:gpt-5.6-sol")
        self.assertEqual(family["status"], "CORRECT")
        self.assertEqual(family["action"], "none")

    def test_17_reserved_output_semantics_dynamic_by_default(self):
        limits = mcr.effective_limits({"contextWindow": 1050000, "maxOutputTokens": 128000})
        self.assertEqual(limits["reserved_output_tokens"], "REQUEST_DYNAMIC")
        self.assertEqual(limits["usable_input_tokens"], 1050000)
        self.assertEqual(limits["max_output_tokens"], 128000)


if __name__ == "__main__":
    unittest.main()
