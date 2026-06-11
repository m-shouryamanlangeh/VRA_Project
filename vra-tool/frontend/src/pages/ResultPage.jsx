import { Link, useLocation, useSearchParams } from "react-router-dom";

// Severity → badge classes (consistent with the PDF: HIGH red, MEDIUM amber,
// LOW/clean green).
const SEV_BADGE = {
  HIGH: "bg-red-100 text-red-800 border-red-200",
  MEDIUM: "bg-amber-100 text-amber-800 border-amber-200",
  LOW: "bg-emerald-100 text-emerald-800 border-emerald-200",
  NONE: "bg-emerald-100 text-emerald-800 border-emerald-200",
};

const OVERALL_BANNER = {
  HIGH: "bg-red-50 border-red-200 text-red-800",
  MEDIUM: "bg-amber-50 border-amber-200 text-amber-800",
  LOW: "bg-emerald-50 border-emerald-200 text-emerald-800",
  NONE: "bg-emerald-50 border-emerald-200 text-emerald-800",
};

function SeverityPill({ severity }) {
  const cls = SEV_BADGE[severity] || SEV_BADGE.LOW;
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold border ${cls}`}>
      {severity}
    </span>
  );
}

export default function ResultPage() {
  const [params] = useSearchParams();
  const location = useLocation();
  const state = location.state || {};

  const screening = state.screening || null;
  const pdf = state.pdf || params.get("pdf") || "";
  const vendor = state.vendor || params.get("vendor") || "";

  const counts = screening?.counts || { HIGH: 0, MEDIUM: 0, LOW: 0 };
  const findings = screening?.findings || [];
  const overall = screening?.overall || "NONE";

  const DownloadBtn = () =>
    pdf ? (
      <a
        href={pdf}
        className="inline-block px-5 py-2.5 rounded-lg bg-paytm-blue text-white font-medium hover:opacity-90"
      >
        Download PDF
      </a>
    ) : null;

  return (
    <main className="max-w-3xl mx-auto px-6 py-10">
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-paytm-dark">
            Adverse-Media Screening
          </h1>
          {vendor ? (
            <p className="text-slate-600 mt-1">
              <span className="font-semibold">{vendor}</span>
              {screening?.date_of_search ? (
                <span className="text-slate-400">
                  {" "}
                  · {screening.date_of_search}
                </span>
              ) : null}
            </p>
          ) : null}
        </div>
        <DownloadBtn />
      </div>

      {!screening ? (
        // Refresh / direct-link fallback: state was lost, only the PDF remains.
        <div className="bg-white rounded-xl border border-slate-100 shadow p-6 text-center">
          <div className="text-3xl mb-2">✅</div>
          <p className="text-paytm-dark font-medium mb-4">Report ready.</p>
          {pdf ? (
            <DownloadBtn />
          ) : (
            <p className="text-red-600">
              On-screen result expired — please generate again.
            </p>
          )}
        </div>
      ) : (
        <>
          {/* Overall verdict */}
          <div
            className={`rounded-xl border p-5 mb-6 ${OVERALL_BANNER[overall] || OVERALL_BANNER.NONE}`}
          >
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-lg font-bold">
                {overall === "NONE"
                  ? "No adverse findings"
                  : `Overall risk: ${overall}`}
              </span>
              <span className="text-sm font-medium">
                {screening.total} negative{" "}
                {screening.total === 1 ? "finding" : "findings"}
                {"  ·  "}
                <span className="text-red-700">HIGH {counts.HIGH}</span>
                {"  ·  "}
                <span className="text-amber-700">MEDIUM {counts.MEDIUM}</span>
                {"  ·  "}
                <span className="text-emerald-700">LOW {counts.LOW}</span>
              </span>
            </div>
          </div>

          {/* Findings */}
          {findings.length === 0 ? (
            <div className="bg-white rounded-xl border border-slate-100 shadow p-8 text-center">
              <div className="text-3xl mb-2">✅</div>
              <p className="text-emerald-700 font-medium">
                No adverse media or negative findings surfaced for{" "}
                <strong>{vendor}</strong> in OSINT screening on this date.
              </p>
              <p className="text-xs text-slate-500 mt-3">
                Reflects open-source signal only on the date of search; not a
                clearance. Re-screen periodically and on any material change.
              </p>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-slate-100 shadow overflow-hidden">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-slate-50 text-paytm-dark">
                    <th className="text-left p-3 font-semibold w-24">Severity</th>
                    <th className="text-left p-3 font-semibold">
                      Negative news / finding
                    </th>
                    <th className="text-left p-3 font-semibold w-28">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.map((f, i) => (
                    <tr
                      key={i}
                      className="border-t border-slate-100 align-top"
                    >
                      <td className="p-3">
                        <SeverityPill severity={f.severity} />
                      </td>
                      <td className="p-3 text-slate-700">
                        {f.category ? (
                          <span className="text-slate-400 font-medium">
                            [{f.category}]{" "}
                          </span>
                        ) : null}
                        {f.title}
                      </td>
                      <td className="p-3">
                        {f.source ? (
                          <a
                            href={f.source}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-paytm-blue underline break-words"
                          >
                            {f.source_label || "link"}
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <p className="mt-8">
        <Link to="/" className="text-paytm-blue underline">
          Screen another vendor
        </Link>
      </p>
    </main>
  );
}
