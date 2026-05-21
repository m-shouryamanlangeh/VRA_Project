import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import * as XLSX from "xlsx";
import { useToast } from "../ToastContext.jsx";
import { apiFetch, apiUrl } from "../api.js";

const ORG_TYPES = [
  "",
  "Proprietorship",
  "Partnership",
  "LLP",
  "Private Limited",
  "Public Limited",
];

export default function HomePage() {
  const { showToast } = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const [tab, setTab] = useState("generate");
  const [form, setForm] = useState({ vendor_name: "", gst: "", org_type: "" });
  const [progressSteps, setProgressSteps] = useState([]);
  const [progressText, setProgressText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [batchFile, setBatchFile] = useState(null);
  const [batchPreview, setBatchPreview] = useState(null);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchPct, setBatchPct] = useState(0);
  const [batchStatus, setBatchStatus] = useState("");
  const [batchDownload, setBatchDownload] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (location.hash === "#batch-panel") setTab("batch");
  }, [location.hash]);

  function update(e) {
    const { name, value } = e.target;
    setForm((f) => ({ ...f, [name]: name === "gst" ? value.toUpperCase() : value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setProgressSteps([]);
    setProgressText("Searching open sources…");

    try {
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((s) => [...s, "Searching open sources…"]);
      setProgressText("Gathering OSINT evidence…");
      await new Promise((r) => setTimeout(r, 400));
      setProgressSteps((s) => [...s, "Gathering OSINT evidence…"]);
      setProgressText("Validating findings…");

      const res = await apiFetch("/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vendor_name: form.vendor_name,
          gst: form.gst || "",
          org_type: form.org_type,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || res.statusText || "Generation failed");
      }
      setProgressSteps((s) => [...s, "Validating findings…"]);
      setProgressText("Generating PDF…");
      setProgressSteps((s) => [...s, "Generating PDF…"]);
      setProgressText("Done");

      const params = new URLSearchParams({
        pdf: data.pdf_url ? apiUrl(data.pdf_url) : "",
        vendor: form.vendor_name,
        audit_id: String(data.audit_id || ""),
      });
      navigate("/result?" + params.toString());
    } catch (err) {
      showToast(err.message || String(err), false);
      setProgressSteps([]);
      setProgressText("");
    } finally {
      setSubmitting(false);
    }
  }

  function pickFile(file) {
    if (!file) return;
    setBatchFile(file);
    setBatchDownload(null);

    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const wb = XLSX.read(ev.target.result, { type: "binary" });
        const sheet = wb.Sheets[wb.SheetNames[0]];
        const rows = XLSX.utils.sheet_to_json(sheet, { header: 1 });
        const head = rows[0] || [];
        const body = rows.slice(1, 6);
        setBatchPreview({ head, body });
      } catch (e) {
        showToast("Could not read Excel: " + e.message, false);
      }
    };
    reader.readAsBinaryString(file);
  }

  async function runBatch() {
    if (!batchFile) return;
    setBatchRunning(true);
    setBatchPct(10);
    setBatchStatus("Processing… (this may take several minutes)");
    setBatchDownload(null);

    try {
      const fd = new FormData();
      fd.append("file", batchFile);
      const res = await apiFetch("/generate/batch", { method: "POST", body: fd });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      setBatchPct(100);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      setBatchDownload(url);
      const errH = res.headers.get("X-VRA-Batch-Errors");
      setBatchStatus(
        "ZIP ready." + (errH && errH !== "0" ? " Some rows failed (" + errH + ")." : ""),
      );
      showToast("Batch ZIP downloaded", true);
    } catch (e) {
      showToast(e.message || String(e), false);
      setBatchStatus("");
    } finally {
      setBatchRunning(false);
    }
  }

  return (
    <main className="max-w-3xl mx-auto px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-paytm-dark">Vendor Risk Assessment</h1>
        <p className="text-slate-600 mt-1">
          Generate comprehensive risk reports via OSINT
        </p>
      </div>

      <div className="flex gap-2 mb-6 border-b border-slate-200">
        <button
          type="button"
          className={"tab-btn" + (tab === "generate" ? " active" : "")}
          onClick={() => setTab("generate")}
        >
          Generate
        </button>
        <button
          type="button"
          className={"tab-btn" + (tab === "batch" ? " active" : "")}
          onClick={() => setTab("batch")}
        >
          Batch
        </button>
      </div>

      {tab === "generate" && (
        <section className="space-y-6">
          <form
            onSubmit={handleSubmit}
            className="bg-white rounded-xl shadow border border-slate-100 p-6 space-y-5"
          >
            <div>
              <label className="block text-sm font-medium text-paytm-dark mb-1">
                Vendor Name *
              </label>
              <input
                name="vendor_name"
                value={form.vendor_name}
                onChange={update}
                required
                maxLength={512}
                placeholder="SHARP PENCIL PRODUCTIONS"
                className="w-full rounded-lg border border-slate-200 px-3 py-2 focus:ring-2 focus:ring-paytm-blue focus:border-paytm-blue outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-paytm-dark mb-1">
                GST Number
              </label>
              <input
                name="gst"
                value={form.gst}
                onChange={update}
                maxLength={15}
                className="uppercase w-full rounded-lg border border-slate-200 px-3 py-2 focus:ring-2 focus:ring-paytm-blue outline-none"
                placeholder="27AAAAA0000A1Z5 (optional)"
                title="15-character Indian GSTIN, or leave blank for name-only web OSINT"
              />
              <p className="text-xs text-slate-500 mt-1">
                If unknown, leave blank — the tool will search open web and news using
                the vendor name.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-paytm-dark mb-1">
                Organization Type
              </label>
              <select
                name="org_type"
                value={form.org_type}
                onChange={update}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 focus:ring-2 focus:ring-paytm-blue outline-none bg-white"
              >
                {ORG_TYPES.map((o) => (
                  <option key={o || "unknown"} value={o}>
                    {o || "Unknown / not specified"}
                  </option>
                ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">
                Leave as unknown if unsure; the report still runs on OSINT.
              </p>
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="submit"
                disabled={submitting}
                className="px-5 py-2.5 rounded-lg bg-paytm-blue text-white font-medium hover:opacity-90 disabled:opacity-50"
              >
                Generate VRA Report
              </button>
              <button
                type="reset"
                onClick={() =>
                  setForm({ vendor_name: "", gst: "", org_type: "" })
                }
                className="px-5 py-2.5 rounded-lg border border-slate-200 text-paytm-dark font-medium hover:bg-slate-50"
              >
                Reset
              </button>
            </div>
          </form>

          <p className="text-sm text-slate-500">⏳ Estimated time: 30–60 seconds</p>

          {(submitting || progressSteps.length > 0) && (
            <div className="bg-white rounded-xl border border-slate-100 p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="spinner h-8 w-8 border-2 border-paytm-blue border-t-transparent rounded-full" />
                <span className="text-sm font-medium text-paytm-dark">
                  {progressText}
                </span>
              </div>
              <ul className="text-xs text-slate-600 space-y-1">
                {progressSteps.map((s, i) => (
                  <li key={i}>✓ {s}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {tab === "batch" && (
        <section className="space-y-6">
          <div
            className={
              "border-2 border-dashed rounded-xl p-10 text-center bg-white cursor-pointer transition-colors " +
              (dragActive
                ? "border-paytm-blue"
                : "border-slate-300 hover:border-paytm-blue")
            }
            onClick={() => fileInputRef.current && fileInputRef.current.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragActive(false);
              if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]);
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xlsm"
              className="hidden"
              onChange={(e) => e.target.files[0] && pickFile(e.target.files[0])}
            />
            <p className="text-paytm-dark font-medium">
              Drag & drop Excel (.xlsx) here
            </p>
            <p className="text-sm text-slate-500 mt-1">
              Columns:{" "}
              <code className="bg-slate-100 px-1 rounded">vendor_name</code>,{" "}
              <code className="bg-slate-100 px-1 rounded">org_type</code> (optional{" "}
              <code className="bg-slate-100 px-1 rounded">gst</code>)
            </p>
            {batchFile && (
              <p className="text-xs text-slate-500 mt-2">
                Selected: <strong>{batchFile.name}</strong>
              </p>
            )}
          </div>

          {batchPreview && (
            <div className="bg-white rounded-xl border border-slate-100 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr>
                    {batchPreview.head.map((h, i) => (
                      <th
                        key={i}
                        className="text-left p-2 border border-slate-200 bg-slate-50"
                      >
                        {h == null ? "" : String(h)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {batchPreview.body.map((row, rIdx) => (
                    <tr key={rIdx}>
                      {batchPreview.head.map((_, cIdx) => (
                        <td key={cIdx} className="p-2 border border-slate-100">
                          {row[cIdx] == null ? "" : String(row[cIdx])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex items-center gap-4">
            <button
              type="button"
              disabled={!batchFile || batchRunning}
              onClick={runBatch}
              className="px-5 py-2.5 rounded-lg bg-paytm-blue text-white font-medium disabled:opacity-40"
            >
              {batchRunning ? "Generating…" : "Generate All"}
            </button>
            {(batchRunning || batchPct > 0) && (
              <div className="flex-1">
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-paytm-blue transition-all"
                    style={{ width: batchPct + "%" }}
                  />
                </div>
                <p className="text-xs text-slate-600 mt-1">{batchStatus}</p>
              </div>
            )}
          </div>

          {batchDownload && (
            <a
              href={batchDownload}
              download="vra_batch_reports.zip"
              className="text-paytm-blue font-medium underline"
            >
              Download ZIP
            </a>
          )}
        </section>
      )}
    </main>
  );
}
