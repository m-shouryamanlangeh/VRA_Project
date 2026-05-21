import { Link, useSearchParams } from "react-router-dom";

export default function ResultPage() {
  const [params] = useSearchParams();
  const pdf = params.get("pdf") || "";
  const vendor = params.get("vendor") || "";

  return (
    <main className="max-w-xl mx-auto px-6 py-16 text-center">
      <div className="text-4xl mb-4">✅</div>
      <h1 className="text-2xl font-bold text-paytm-dark mb-2">Report ready</h1>
      {vendor ? <p className="text-slate-600 mb-6">{vendor}</p> : null}
      {pdf ? (
        <a
          href={pdf}
          className="inline-block px-6 py-3 rounded-lg bg-paytm-blue text-white font-medium hover:opacity-90"
        >
          Download PDF
        </a>
      ) : (
        <p className="text-red-600">Missing PDF link.</p>
      )}
      <p className="mt-8">
        <Link to="/" className="text-paytm-blue underline">
          Generate another
        </Link>
      </p>
    </main>
  );
}
