from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import pytest
import rfc8785
from pydantic import ValidationError

from boundary.domain.definitions import Phase1FaultDefinition
from boundary.domain.regression import (
    RegressionArtifactV1,
    RegressionLocalizationV1,
    TestedInputV1 as RegressionTestedInputV1,
)
from boundary.evidence.canonical import FAULT_SPEC_V1_SHA256
from boundary.injection.fault_spec import FAULT_SPEC_V1_ID
from boundary.regression.materializer import (
    RegressionIntegrityError,
    canonicalize_regression_artifact,
)
from boundary.regression.rerun import (
    build_pre_invocation_report,
    canonicalize_document,
    definition_from_artifact,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "fault-spec-v1.json"


def _artifact() -> RegressionArtifactV1:
    fault = Phase1FaultDefinition.model_validate_json(FIXTURE_PATH.read_bytes())
    tested_input = RegressionTestedInputV1(query="phase1 lookup")
    tested_input_digest = sha256(
        rfc8785.dumps(tested_input.model_dump(mode="json"))
    ).hexdigest()
    regression_case_id = uuid4()
    localization = RegressionLocalizationV1(
        assertion_id="P1.RETRY_LIMIT",
        boundary_event_id=uuid4(),
        boundary="retry_control",
        retry_ordinal=2,
        supporting_evidence_references=[],
    )
    core = {
        "artifact_schema_version": 1,
        "regression_case_id": regression_case_id,
        "source_campaign_id": uuid4(),
        "source_run_id": uuid4(),
        "source_trace_id": uuid4(),
        "source_evidence_set_id": uuid4(),
        "source_evidence_set_digest": "1" * 64,
        "source_analysis_id": uuid4(),
        "source_analysis_digest": "2" * 64,
        "original_tested_agent_id": "boundary.sample-agent",
        "original_tested_agent_version": "vulnerable-v1",
        "contract_version": "1",
        "scenario_id": "phase1.tool-timeout",
        "scenario_version": 1,
        "tested_input": tested_input.model_dump(mode="json"),
        "tested_input_digest": tested_input_digest,
        "fault_spec_id": FAULT_SPEC_V1_ID,
        "fault_definition": fault.model_dump(mode="json"),
        "fault_definition_digest": FAULT_SPEC_V1_SHA256,
        "source_fault_id": uuid4(),
        "analyzer_version": "boundary.phase1.tool-timeout.analyzer/v1",
        "assertion_set_version": "boundary.phase1.tool-timeout.assertions/v1",
        "policy_version": "boundary.phase1.tool-timeout.policy/v1",
        "failed_assertion_identifiers": ["P1.RETRY_LIMIT"],
        "localization": localization.model_dump(mode="json"),
        "supporting_evidence_references": [],
    }
    json_core = json.loads(json.dumps(core, default=str))
    digest = sha256(rfc8785.dumps(json_core)).hexdigest()
    return RegressionArtifactV1.model_validate_json(
        rfc8785.dumps({**json_core, "integrity_digest": digest})
    )


def test_regression_artifact_is_strict_digest_valid_and_repeatable() -> None:
    artifact = _artifact()
    first = canonicalize_regression_artifact(artifact)
    second = canonicalize_regression_artifact(artifact)

    assert first == second
    assert first[1] == artifact.integrity_digest
    assert b"created_at" not in first[0]
    with pytest.raises(ValidationError):
        RegressionArtifactV1.model_validate(
            {**artifact.model_dump(mode="python"), "unknown": True}
        )


def test_integrity_digest_covers_every_immutable_artifact_field() -> None:
    artifact = _artifact()
    changed = artifact.model_copy(update={"policy_version": "changed/v2"})

    with pytest.raises(RegressionIntegrityError, match="integrity digest"):
        canonicalize_regression_artifact(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("regression_case_id", uuid4()),
        ("contract_version", "2"),
        ("scenario_id", "phase1.changed"),
        ("scenario_version", 2),
        ("tested_agent_id", "other-agent"),
        ("tested_input", RegressionTestedInputV1(query="changed")),
        ("tested_input_digest", "3" * 64),
        ("fault_spec_id", uuid4()),
        ("fault_definition_digest", "4" * 64),
        ("analyzer_version", "changed-analyzer/v2"),
        ("assertion_set_version", "changed-assertions/v2"),
        ("policy_version", "changed-policy/v2"),
    ],
)
def test_every_test_definition_drift_is_a_pre_invocation_mismatch(
    field,
    value,
) -> None:
    artifact = _artifact()
    proposed = definition_from_artifact(
        artifact, tested_agent_version="fixed-v1"
    ).model_copy(update={field: value})
    report = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="version_comparison",
        proposed=proposed,
    )

    assert any(
        row.result == "MISMATCH" and row.field_identifier == field
        for row in report.rows
    )


def test_normalized_fault_definition_drift_is_an_explicit_mismatch() -> None:
    artifact = _artifact()
    drifted_fault = artifact.fault_definition.model_copy(
        update={"injected_hold_ms": 999}
    )
    proposed = definition_from_artifact(
        artifact, tested_agent_version="fixed-v1"
    ).model_copy(update={"fault_definition": drifted_fault})

    report = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="version_comparison",
        proposed=proposed,
    )

    definition_row = next(
        row
        for row in report.rows
        if row.field_identifier == "fault_definition"
    )
    digest_row = next(
        row
        for row in report.rows
        if row.field_identifier == "fault_definition_digest"
    )
    assert definition_row.result == "MISMATCH"
    assert digest_row.result == "MATCH"


def test_version_mode_rules_are_explicit() -> None:
    artifact = _artifact()
    same = definition_from_artifact(
        artifact, tested_agent_version="vulnerable-v1"
    )
    reproduction = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="reproduction",
        proposed=same,
    )
    comparison = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="version_comparison",
        proposed=same,
    )
    fixed = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="version_comparison",
        proposed=definition_from_artifact(
            artifact, tested_agent_version="fixed-v1"
        ),
    )

    assert next(
        row for row in reproduction.rows if row.field_identifier == "tested_agent_version"
    ).result == "MATCH"
    assert next(
        row for row in comparison.rows if row.field_identifier == "tested_agent_version"
    ).result == "MISMATCH"
    assert next(
        row for row in fixed.rows if row.field_identifier == "tested_agent_version"
    ).result == "PERMITTED_DIFFERENCE"
    assert all(row.result != "MISMATCH" for row in fixed.rows)


def test_invariance_document_never_serializes_a_capability_secret() -> None:
    artifact = _artifact()
    report = build_pre_invocation_report(
        rerun_id=uuid4(),
        artifact=artifact,
        mode="version_comparison",
        proposed=definition_from_artifact(
            artifact, tested_agent_version="fixed-v1"
        ),
    )
    secret = "private-capability-secret"
    canonical, digest = canonicalize_document(report)

    assert secret.encode() not in canonical
    assert json.loads(canonical)["report_phase"] == "pre_invocation"
    assert digest == sha256(canonical).hexdigest()
