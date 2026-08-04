import type { RegressionCase } from "../api/types";
import { AppLink } from "./AppLink";
import { DefinitionList, Identifier, PlainData, ReferenceList } from "./Primitives";

export function RegressionProvenance({ regression, onSelectEvidence }: {
  regression: RegressionCase;
  onSelectEvidence: (evidenceId: string) => void;
}) {
  const artifact = regression.artifact;
  return (
    <section className="panel" aria-labelledby="provenance-heading">
      <p className="eyebrow">Immutable source artifact</p>
      <h2 id="provenance-heading">Regression provenance</h2>
      <DefinitionList rows={[
        ["Regression case", <Identifier>{regression.regression_case_id}</Identifier>],
        ["Integrity digest", <Identifier>{regression.integrity_digest}</Identifier>],
        ["Source campaign", <AppLink href={`/?campaign=${artifact.source_campaign_id}`}><Identifier>{artifact.source_campaign_id}</Identifier></AppLink>],
        ["Source run", <AppLink href={`/runs/${artifact.source_run_id}`}><Identifier>{artifact.source_run_id}</Identifier></AppLink>],
        ["Source trace", <Identifier>{artifact.source_trace_id}</Identifier>],
        ["Evidence set", <Identifier>{artifact.source_evidence_set_id}</Identifier>],
        ["Evidence digest", <Identifier>{artifact.source_evidence_set_digest}</Identifier>],
        ["Analysis", <Identifier>{artifact.source_analysis_id}</Identifier>],
        ["Analysis digest", <Identifier>{artifact.source_analysis_digest}</Identifier>],
        ["Original tested agent", `${artifact.original_tested_agent_id} · ${artifact.original_tested_agent_version}`],
        ["Contract", artifact.contract_version],
        ["Scenario", `${artifact.scenario_id} v${artifact.scenario_version}`],
      ]} />
      <div className="two-column-data">
        <div><h3>Tested input</h3><PlainData value={artifact.tested_input} /><p><span className="label">Digest</span> <Identifier>{artifact.tested_input_digest}</Identifier></p></div>
        <div><h3>Fault definition</h3><PlainData value={artifact.fault_definition} /><p><span className="label">Digest</span> <Identifier>{artifact.fault_definition_digest}</Identifier></p></div>
      </div>
      <h3>Failed assertions</h3>
      <ul className="tag-list">{artifact.failed_assertion_identifiers.map((id) => <li key={id}><code>{id}</code></li>)}</ul>
      <h3>Localization</h3>
      <p><code>{artifact.localization.assertion_id}</code> first diverged at <code>{artifact.localization.boundary}</code>, retry ordinal <strong>{artifact.localization.retry_ordinal}</strong>.</p>
      <ReferenceList references={artifact.supporting_evidence_references} onSelect={onSelectEvidence} />
      <DefinitionList rows={[
        ["Analyzer", artifact.analyzer_version],
        ["Assertion set", artifact.assertion_set_version],
        ["Policy", artifact.policy_version],
      ]} />
    </section>
  );
}
