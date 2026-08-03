from __future__ import annotations

from uuid import uuid4

import rfc8785

from boundary.domain.evidence import EvidenceManifestV1
from boundary.evidence.finalizer import canonicalize_manifest


def test_manifest_normalization_and_digest_are_repeatable() -> None:
    manifest = EvidenceManifestV1(
        schema_version=1,
        evidence_set_id=uuid4(),
        run_id=uuid4(),
        trace_id=uuid4(),
        run_role="control",
        contract_version="1",
        scenario_id="phase1.tool-timeout",
        scenario_version=1,
        expected_tested_agent_id="boundary.sample-agent",
        expected_tested_agent_version="vulnerable-v1",
        reported_tested_agent_id="boundary.sample-agent",
        reported_tested_agent_version="vulnerable-v1",
        operational_status="completed",
        control_run_id=None,
        fault_spec_id=None,
        fault_spec_digest=None,
        fault_id=None,
        capability_binding=None,
        cutoff_reason="target_terminal_watermark",
        target_producer_cursor=0,
        target_final_watermark=0,
        accepted_evidence=[],
        timeout_activations=[],
        cutoff_markers=[],
        finalizer_identity="boundary.phase1.evidence-finalizer/v1",
    )
    first = canonicalize_manifest(manifest)
    second = canonicalize_manifest(manifest)
    assert first == second
    assert rfc8785.dumps(manifest.model_dump(mode="json")) == first[0]
    assert b"created_at" not in first[0]
    assert b"observed_at" not in first[0]
