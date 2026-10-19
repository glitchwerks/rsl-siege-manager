import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { cn } from "../lib/utils";
import { Info, LogOut, Shield } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { fetchConfig } from "../api/config";
import ChangelogDropdown from "./ChangelogDropdown";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-md px-3 py-2 text-sm font-medium transition-colors",
    isActive
      ? "bg-slate-100 text-slate-900"
      : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
  );

export default function Layout() {
  const { user, logout } = useAuth();

  const { data: config, isError } = useQuery({
    queryKey: ["app-config"],
    queryFn: fetchConfig,
    // Config is static for the lifetime of the page — refetch only on mount.
    staleTime: Infinity,
    retry: false,
  });

  // Fail-closed: if the config endpoint is unreachable, treat as auth-enabled
  // (production mode) and suppress the demo banner.
  const isDemo = !isError && config?.auth_disabled === true;

  return (
    <div className="min-h-screen bg-slate-50">
      {isDemo && (
        <div className="bg-amber-400 px-4 py-1.5 text-center text-xs font-medium text-amber-900">
          Demo mode — authentication disabled. Set{" "}
          <code className="font-mono">AUTH_DISABLED=false</code> for real
          deployments.
        </div>
      )}
      <nav className="border-b border-slate-200 bg-white shadow-sm">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-14 items-center gap-6">
            <div className="flex items-center gap-2 text-slate-900">
              <Shield className="h-5 w-5 text-violet-600" />
              <span className="text-sm font-semibold">RSL Siege Manager</span>
            </div>
            <div className="flex flex-1 items-center gap-1">
              <NavLink to="/sieges" className={navLinkClass}>
                Sieges
              </NavLink>
              <NavLink to="/members" className={navLinkClass}>
                Members
              </NavLink>
              <NavLink to="/post-priorities" className={navLinkClass}>
                Posts
              </NavLink>
              <NavLink to="/guide" className={navLinkClass}>
                Guide
              </NavLink>
              {/* System link and user info pushed to the far right */}
              <div className="ml-auto flex items-center gap-2">
                <NavLink
                  to="/system"
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                      isActive
                        ? "bg-slate-100 text-slate-900"
                        : "text-slate-400 hover:bg-slate-50 hover:text-slate-600"
                    )
                  }
                >
                  <Info className="h-3.5 w-3.5" />
                  System
                </NavLink>
                <ChangelogDropdown />
                {user && (
                  <>
                    <span className="text-sm text-slate-500">{user.name}</span>
                    <button
                      onClick={logout}
                      className="flex items-center gap-1 rounded-md px-2 py-1.5 text-sm text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-600"
                      title="Sign out"
                    >
                      <LogOut className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </nav>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
