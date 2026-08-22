"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

type Result = Record<string, string | number | null | undefined>;

type SearchResponse = {
  source: string;
  query: string;
  count: number;
  results: Result[];
  provenance: string;
};

export default function DatabaseSearch() {
  const [source, setSource] = useState("rcsb");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const params = new URLSearchParams({ source, query });
      setResult(await api.get<SearchResponse>(`/databases/search?${params}`));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <form className="row" onSubmit={search}>
        <select value={source} onChange={(event) => setSource(event.target.value)} style={{ width: 150 }}>
          <option value="rcsb">RCSB PDB</option>
          <option value="pubchem">NIH PubChem</option>
          <option value="chembl">EMBL-EBI ChEMBL</option>
        </select>
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="protein or compound name"
          aria-label="database search query"
          style={{ flex: 1, minWidth: 180 }}
        />
        <button type="submit" disabled={busy}>
          {busy ? "searching..." : "search"}
        </button>
      </form>
      {error && <p className="err">{error}</p>}
      {result && (
        <div style={{ marginTop: 10 }}>
          <p className="hint">
            {result.count} result{result.count === 1 ? "" : "s"} from {result.provenance}
          </p>
          {result.results.length > 0 ? (
            <ul className="search-results">
              {result.results.map((item, index) => (
                <li key={String(item.id || item.CID || item.chembl_id || index)}>
                  {item.url ? (
                    <a href={String(item.url)} target="_blank" rel="noreferrer">
                      {String(item.name || item.Title || item.id || item.chembl_id || item.CID)}
                    </a>
                  ) : (
                    String(item.name || item.Title || item.id || item.chembl_id || item.CID)
                  )}
                  {item.score !== undefined && <span className="muted"> (score {item.score})</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No matching records.</p>
          )}
        </div>
      )}
    </div>
  );
}
