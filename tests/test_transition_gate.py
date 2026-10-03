"""Synthetic publication controls using the existing Core transition authority."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from ci.transition_gate import (
    TRUSTED_CONTROL_ID, TRUSTED_CORE_ID, baseline_for_event, core_pin, prepare, validate_pair, git_contract,
)

# CI supplies the exact prior Core checkout. Local runs may use a deliberately
# selected development checkout; such runs are not deployment provenance proof.
sys.path.insert(0, os.environ["TRUSTED_CORE_ROOT"])


class TransitionGateTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "schema_version": "0.4", "document_type": "deployment_binding",
            "updated_at": "2026-10-03T00:00:00+00:00",
            "deployment": {"id": "dep-ci-transition", "topology": "split", "epoch": 9, "write_state": "active"},
            "core": {"repository_id": TRUSTED_CORE_ID, "commit": "a" * 40},
        }
        self.event = {
            "repository": {"id": TRUSTED_CONTROL_ID}, "ref": "refs/heads/main",
            "created": False, "deleted": False, "forced": False,
            "before": "b" * 40, "after": "c" * 40,
        }

    def check(self, previous, candidate):
        validate_pair(yaml.safe_dump(previous), yaml.safe_dump(candidate))

    def rejected(self, previous, candidate):
        with self.assertRaises((ValueError, RuntimeError)):
            self.check(previous, candidate)

    def test_noop_and_metadata_only_publications(self):
        self.check(self.base, self.base)
        other = copy.deepcopy(self.base)
        other["updated_at"] = "2026-10-03T01:00:00+00:00"
        self.check(self.base, other)

    def test_freeze_promote_activate_as_separate_publications(self):
        frozen = copy.deepcopy(self.base)
        frozen["deployment"]["write_state"] = "frozen"
        promoted = copy.deepcopy(frozen)
        promoted["deployment"]["epoch"] = 10
        promoted["core"]["commit"] = "d" * 40
        active = copy.deepcopy(promoted)
        active["deployment"]["write_state"] = "active"
        for old, new in ((self.base, frozen), (frozen, promoted), (promoted, active)):
            self.check(old, new)

    def test_snapshot_valid_direct_active_promotion_is_rejected(self):
        from scripts.validate_learning_os import validate_deployment_contract_document
        other = copy.deepcopy(self.base)
        other["deployment"]["epoch"] = 10
        other["core"]["commit"] = "d" * 40
        self.assertFalse([f for f in validate_deployment_contract_document(other) if f.severity == "error"])
        self.rejected(self.base, other)

    def test_compressed_frozen_promotion_and_activation_is_rejected(self):
        self.base["deployment"]["write_state"] = "frozen"
        other = copy.deepcopy(self.base)
        other["deployment"].update(epoch=10, write_state="active")
        other["core"]["commit"] = "d" * 40
        self.rejected(self.base, other)

    def test_epoch_skip_rollback_or_change_without_pin_is_rejected(self):
        self.base["deployment"]["write_state"] = "frozen"
        for epoch, pin in ((11, "d"*40), (8, "d"*40), (10, "a"*40)):
            with self.subTest(epoch=epoch, pin=pin):
                other = copy.deepcopy(self.base)
                other["deployment"]["epoch"] = epoch
                other["core"]["commit"] = pin
                self.rejected(self.base, other)

    def test_identity_and_boolean_schema_regressions(self):
        for section, key, value in (("core", "repository_id", TRUSTED_CORE_ID+1),
                                    ("core", "repository_id", True),
                                    ("deployment", "epoch", True),
                                    ("deployment", "id", "another-deployment")):
            with self.subTest(key=key):
                other=copy.deepcopy(self.base)
                other[section][key]=value
                self.rejected(self.base,other)

    def test_duplicate_keys_fail_closed(self):
        text=yaml.safe_dump(self.base)
        with self.assertRaises((ValueError, RuntimeError)):
            validate_pair(text, text + "schema_version: '0.4'\n")

    def test_uses_push_before_and_pr_base(self):
        self.assertEqual(baseline_for_event("push", self.event), "b" * 40)
        event={"repository":{"id":TRUSTED_CONTROL_ID},
               "pull_request":{"base":{"sha":"e"*40, "ref":"main"}, "head":{"sha":"f"*40}}}
        self.assertEqual(baseline_for_event("pull_request", event), "e" * 40)

    def test_missing_or_untrusted_event_baselines_fail_closed(self):
        for change in ({"before":"0"*40}, {"before":"main"}, {"forced":True},
                       {"created":True}, {"deleted":True}, {"repository":{"id":1}},
                       {"ref":"refs/heads/other"}, {"forced":None}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    baseline_for_event("push",dict(self.event,**change))
        with self.assertRaises(ValueError):
            baseline_for_event("workflow_dispatch",self.event)
        with self.assertRaises(ValueError):
            baseline_for_event("pull_request",self.event)

    def test_prepare_reads_accepted_base_not_candidate_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"previous.yaml"
            with patch("ci.transition_gate.git", side_effect=["c"*40, "", str(len(yaml.safe_dump(self.base).encode())), yaml.safe_dump(self.base)]) as git:
                pin=prepare(Path(temp),"push",self.event,"c"*40,path)
            self.assertEqual(pin,"a"*40)
            self.assertEqual(git.call_args.args[-1], "b"*40+":deployment.yaml")
            self.assertEqual(core_pin(path.read_text()),pin)

    def test_prepare_rejects_checkout_or_after_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"previous.yaml"
            with self.assertRaises(ValueError):
                prepare(Path(temp),"push",self.event,"d"*40,path)
            with patch("ci.transition_gate.git",return_value="d"*40):
                with self.assertRaises(ValueError):
                    prepare(Path(temp),"push",self.event,"c"*40,path)
            self.assertFalse(path.exists())

    def test_existing_validation_check_cannot_silently_skip_failed_gate(self):
        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["validate-runtime-control"]
        self.assertEqual(job["needs"], "validate-transition")
        self.assertIn("always()", job["if"])
        step = job["steps"][0]
        self.assertEqual(step["env"]["TRANSITION_RESULT"], "${{ needs.validate-transition.result }}")
        self.assertEqual(step["run"], 'test "$TRANSITION_RESULT" = success')

    def test_workflow_trust_id_environment_is_declared_and_matches_code(self):
        workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/validate.yml").read_text(encoding="utf-8"))
        self.assertEqual(workflow["env"]["TRUSTED_SELF_REPOSITORY_ID"], str(TRUSTED_CONTROL_ID))
        self.assertEqual(workflow["env"]["TRUSTED_CORE_REPOSITORY_ID"], str(TRUSTED_CORE_ID))

    def test_committed_contract_read_has_a_preconstruction_size_limit(self):
        with patch("ci.transition_gate.git", return_value=str(1024*1024+1)) as git:
            with self.assertRaises(ValueError):
                git_contract(Path("."), "a"*40)
            self.assertEqual(git.call_count, 1)

    def test_committed_contract_preserves_document_whitespace(self):
        text = "  key: value\n\n"
        with patch("ci.transition_gate.git", side_effect=[str(len(text)), text]) as git:
            self.assertEqual(git_contract(Path("."), "a"*40), text)
            self.assertEqual(git.call_args.args[-1], "a"*40 + ":deployment.yaml")
