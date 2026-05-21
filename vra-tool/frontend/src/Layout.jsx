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
          <NavLink
            to="/"
            className="flex items-center gap-2 text-paytm-dark font-semibold text-lg"
          >
            <span className="text-2xl" aria-hidden="true">
              🛡️
            </span>
            <span>
              Paytm <span className="text-paytm-blue">VRA</span> Tool
            </span>
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
