import { useEffect, useMemo, useState } from "react";
import { useToast } from "../ToastContext.jsx";
import { apiFetch, apiUrl } from "../api.js";

const PAGE_SIZE = 50;

export default function AuditPage() {
  const { showToast } = useToast();
  const [filters, setFilters] = useState({ vendor: "", from: "", to: "" });
  const [appliedFilters, setAppliedFilters] = useState({
    vendor: "",
    from: "",
    to: "",
  });
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ items: [], total: 0, page: 1 });
  const [loading, setLoading] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [busy, setBusy] = useState(false);

  const csvHref = useMemo(() => {
    const qs = new URLSearchParams();
    if (appliedFilters.vendor) qs.set("vendor", appliedFilters.vendor);
    if (appliedFilters.from) qs.set("date_from", appliedFilters.from);
    if (appliedFilters.to) qs.set("date_to", appliedFilters.to);
    const tail = qs.toString();
    return apiUrl("/api/audit/export.csv" + (tail ? "?" + tail : ""));
  }, [appliedFilters]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const qs = new URLSearchParams({
          page: String(page),
          page_size: String(PAGE_SIZE),
        });
        if (appliedFilters.vendor) qs.set("vendor", appliedFilters.vendor);
        if (appliedFilters.from) qs.set("date_from", appliedFilters.from);
        if (appliedFilters.to) qs.set("date_to", appliedFilters.to);
        const res = await apiFetch("/api/audit?" + qs.toString());
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || "Failed to load audit");
        if (!cancelled) setData(body);
      } catch (err) {
        if (!cancelled) showToast(err.message || String(err), false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [page, appliedFilters, showToast, reloadKey]);

  function applyFilters() {
    setPage(1);
    setAppliedFilters({ ...filters });
  }

  async function deleteEntry(row) {
    if (busy) return;
    if (!window.confirm(`Delete the history entry for "${row.vendor_name}"?`))
      return;
    setBusy(true);
    try {
      const res = await apiFetch("/api/audit/" + row.id, { method: "DELETE" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Failed to delete entry");
      showToast("History entry deleted", true);
      setReloadKey((k) => k + 1);
    } catch (err) {
      showToast(err.message || String(err), false);
    } finally {
      setBusy(false);
    }
  }

  async function clearHistory() {
    if (busy) return;
    const scoped =
      appliedFilters.vendor || appliedFilters.from || appliedFilters.to;
    const msg = scoped
      ? "Delete all history entries matching the current filters? This cannot be undone."
      : "Delete the ENTIRE search history? This cannot be undone.";
    if (!window.confirm(msg)) return;
    setBusy(true);
    try {
      const qs = new URLSearchParams();
      if (appliedFilters.vendor) qs.set("vendor", appliedFilters.vendor);
      if (appliedFilters.from) qs.set("date_from", appliedFilters.from);
      if (appliedFilters.to) qs.set("date_to", appliedFilters.to);
      const tail = qs.toString();
      const res = await apiFetch("/api/audit" + (tail ? "?" + tail : ""), {
        method: "DELETE",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Failed to clear history");
      showToast(`Deleted ${body.deleted ?? 0} entr${(body.deleted === 1) ? "y" : "ies"}`, true);
      setPage(1);
      setReloadKey((k) => k + 1);
    } catch (err) {
      showToast(err.message || String(err), false);
    } finally {
      setBusy(false);
    }
  }

  const total = data.total || 0;
  const canPrev = page > 1;
  const canNext = page * PAGE_SIZE < total;

  return (
    <main className="max-w-6xl mx-auto px-6 py-10">
      <h1 className="text-2xl font-bold text-paytm-dark mb-6">Audit log</h1>

      <div className="flex flex-wrap gap-3 mb-6 items-end">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Vendor contains</label>
          <input
            value={filters.vendor}
            onChange={(e) => setFilters((f) => ({ ...f, vendor: e.target.value }))}
            className="rounded border border-slate-200 px-2 py-1 text-sm"
            placeholder="Name"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">From (ISO)</label>
          <input
            value={filters.from}
            onChange={(e) => setFilters((f) => ({ ...f, from: e.target.value }))}
            className="rounded border border-slate-200 px-2 py-1 text-sm"
            placeholder="2026-01-01"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">To (ISO)</label>
          <input
            value={filters.to}
            onChange={(e) => setFilters((f) => ({ ...f, to: e.target.value }))}
            className="rounded border border-slate-200 px-2 py-1 text-sm"
            placeholder="2026-12-31"
          />
        </div>
        <button
          type="button"
          onClick={applyFilters}
          className="px-3 py-1.5 rounded bg-paytm-blue text-white text-sm font-medium"
        >
          Filter
        </button>
        <a
          href={csvHref}
          className="px-3 py-1.5 rounded border border-slate-200 text-sm font-medium text-paytm-dark"
        >
          Export CSV
        </a>
        <button
          type="button"
          onClick={clearHistory}
          disabled={busy || total === 0}
          className="px-3 py-1.5 rounded border border-red-200 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-40 ml-auto"
        >
          Clear history
        </button>
      </div>

      <div className="bg-white rounded-xl border border-slate-100 overflow-x-auto shadow-sm">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-paytm-dark">
              <th className="p-2 border-b">Timestamp</th>
              <th className="p-2 border-b">Vendor</th>
              <th className="p-2 border-b">GST</th>
              <th className="p-2 border-b">Type</th>
              <th className="p-2 border-b">Status</th>
              <th className="p-2 border-b">Provider</th>
              <th className="p-2 border-b">PDF</th>
              <th className="p-2 border-b"></th>
            </tr>
          </thead>
          <tbody>
            {(data.items || []).map((row) => {
              const pdfName = row.pdf_path
                ? row.pdf_path.replace(/^output\//, "")
                : "";
              return (
                <tr key={row.id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="p-2 whitespace-nowrap">{row.timestamp}</td>
                  <td className="p-2">{row.vendor_name}</td>
                  <td className="p-2">{row.gst}</td>
                  <td className="p-2">{row.request_type}</td>
                  <td className="p-2">{row.status}</td>
                  <td className="p-2">{row.provider_used}</td>
                  <td className="p-2">
                    {pdfName ? (
                      <a
                        className="text-paytm-blue underline"
                        href={apiUrl("/download/pdf/" + encodeURIComponent(pdfName))}
                      >
                        Download
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="p-2 text-right">
                    <button
                      type="button"
                      onClick={() => deleteEntry(row)}
                      disabled={busy}
                      className="text-red-600 hover:underline disabled:opacity-40"
                      title="Delete this history entry"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              );
            })}
            {!loading && (data.items || []).length === 0 && (
              <tr>
                <td className="p-4 text-center text-slate-500" colSpan={8}>
                  No audit entries.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between items-center mt-4 text-sm text-slate-600">
        <span>
          {loading
            ? "Loading…"
            : `Showing page ${data.page} — ${total} total rows`}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!canPrev}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="px-2 py-1 border rounded disabled:opacity-40"
          >
            Prev
          </button>
          <button
            type="button"
            disabled={!canNext}
            onClick={() => setPage((p) => p + 1)}
            className="px-2 py-1 border rounded disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </main>
  );
}
