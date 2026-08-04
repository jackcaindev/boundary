import type { InvarianceRow } from "../api/types";

function RowTable({ title, rows, empty }: { title: string; rows: InvarianceRow[]; empty: string }) {
  return (
    <section className="invariance-group">
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="empty-copy">{empty}</p> : (
        <div className="table-scroll">
          <table>
            <thead><tr><th>Field</th><th>Source</th><th>Candidate</th><th>Rule</th><th>Result</th></tr></thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.field_identifier}-${index}`}>
                  <th scope="row"><code>{row.field_identifier}</code></th>
                  <td>{row.source_value_or_digest}</td>
                  <td>{row.rerun_value_or_digest}</td>
                  <td>{row.comparison_rule}</td>
                  <td><strong className={`outcome outcome-${row.result.toLowerCase().replaceAll("_", "-")}`}>{row.result}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function InvarianceComparison({ rows, permittedDifferences, mismatches }: {
  rows: InvarianceRow[];
  permittedDifferences: InvarianceRow[];
  mismatches: InvarianceRow[];
}) {
  const matches = rows.filter((row) => row.result === "MATCH");
  return (
    <section className="panel" aria-labelledby="invariance-heading">
      <p className="eyebrow">Server-verified comparison</p>
      <h2 id="invariance-heading">Invariance comparison</h2>
      <RowTable title="Invariant matches" rows={matches} empty="No completed invariant matches yet." />
      <RowTable title="Permitted differences" rows={permittedDifferences} empty="No permitted differences reported." />
      <RowTable title="Mismatches" rows={mismatches} empty="No invariant mismatches reported." />
    </section>
  );
}
