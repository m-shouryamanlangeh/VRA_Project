import { NavLink, Outlet, useLocation } from "react-router-dom";

function navClass({ isActive }) {
  return "nav-pill" + (isActive ? " active" : "");
}

export default function Layout() {
  const location = useLocation();
  const isHome = location.pathname === "/" || location.pathname === "";
  const isBatch = isHome && location.hash === "#batch-panel";

  return (
    <>
      <header className="bg-white border-b border-slate-200 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex flex-wrap items-center justify-between gap-4">
          <NavLink to="/" className="flex items-center gap-3 shrink-0">
            <img
              src="/paytm-logo.svg"
              alt="Paytm"
              className="h-7 w-auto block"
            />
            <div className="h-8 w-px bg-slate-200" aria-hidden="true" />
            <div className="leading-tight">
              <div className="text-[15px] font-semibold text-paytm-dark tracking-tight">
                Vendor Risk Assessment
              </div>
              <div className="text-[10.5px] text-slate-400 font-medium uppercase tracking-[0.12em]">
                Compliance · OSINT
              </div>
            </div>
          </NavLink>
          <nav className="flex flex-wrap items-center gap-1 text-sm font-medium">
            <NavLink to="/" end className={navClass}>
              Generate
            </NavLink>
            <NavLink
              to="/#batch-panel"
              className={"nav-pill" + (isBatch ? " active" : "")}
            >
              Batch
            </NavLink>
            <NavLink to="/audit" className={navClass}>
              Audit
            </NavLink>
            <NavLink to="/settings" className={navClass} title="Settings">
              ⚙️
            </NavLink>
          </nav>
        </div>
      </header>
      <Outlet />
    </>
  );
}
