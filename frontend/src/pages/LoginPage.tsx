import { useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import apiClient from "../api/client";
import { Shield, ChevronLeft } from "lucide-react";

// Errors that map to a specific transient/technical message (not membership denial).
const ERROR_MESSAGES: Record<string, string> = {
  service_unavailable:
    "Login is temporarily unavailable. Please try again in a moment.",
  insufficient_role:
    "You don't have the required Discord role to access this application. Contact your clan leader for access.",
};

// Errors that indicate guild membership denial — trigger the soft-handoff reframe.
const MEMBERSHIP_ERRORS = new Set(["unauthorized"]);

// TODO: extract into a shared MobileBanner component when LandingPage grows one.
function MobileBanner() {
  const isMobile =
    typeof window !== "undefined" && window.innerWidth < 768;
  if (!isMobile) return null;
  return (
    <div
      className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3"
      data-testid="mobile-banner"
    >
      <p className="text-xs leading-snug text-amber-800">
        ⚠ Desktop recommended — the board UI is built for larger screens
      </p>
    </div>
  );
}

export default function LoginPage() {
  const [searchParams] = useSearchParams();
  const error = searchParams.get("error");
  const [isLoading, setIsLoading] = useState(false);

  // Determine whether this error is a membership denial (soft-handoff) or a
  // technical error (generic message). No error → happy path, no banner.
  const isMembershipDenied = error !== null && MEMBERSHIP_ERRORS.has(error);
  const isTechnicalError =
    error !== null && !isMembershipDenied;

  const handleLogin = async () => {
    setIsLoading(true);
    try {
      const { data } = await apiClient.get("/api/auth/login");
      window.location.href = data.url;
    } catch {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
        {/* Logo + title */}
        <div className="flex flex-col items-center gap-2">
          <Shield className="h-8 w-8 text-violet-600" />
          <h1 className="text-xl font-semibold text-slate-900">
            RSL Siege Manager
          </h1>
        </div>

        {/* Membership-denial reframe — soft handoff to self-host */}
        {isMembershipDenied && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3">
            <p className="text-sm leading-snug text-red-700">
              This instance is private to Master&rsquo;s Of Magicka. Sign-in is
              limited to clan members.
            </p>
          </div>
        )}

        {/* Technical / transient error */}
        {isTechnicalError && (
          <div className="rounded-md bg-red-50 p-3 text-center text-sm text-red-700">
            {ERROR_MESSAGES[error!] ??
              "You are not authorized to access this app."}
          </div>
        )}

        {/* Sign-in button — happy path, byte-identical behaviour */}
        <div className="space-y-4">
          {/* Back to Home — always visible; lets users exit without a URL edit */}
          <div className="text-center">
            <Link
              to="/"
              className="inline-flex items-center gap-1 text-sm text-slate-600 transition-colors hover:text-slate-900"
              data-testid="back-to-home-link"
            >
              <ChevronLeft className="h-4 w-4" />
              Back to Home
            </Link>
          </div>

          <button
            onClick={handleLogin}
            disabled={isLoading}
            className="flex w-full items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-medium text-white transition-colors disabled:opacity-50"
            style={{ backgroundColor: "#5865F2" }}
          >
            {isLoading ? "Signing in..." : "Sign in with Discord"}
          </button>

          {/* Always-visible self-host link */}
          <div className="text-center">
            <Link to="/guide" className="text-sm text-violet-700 hover:underline">
              Read the clan guide — no sign-in needed
            </Link>
          </div>
          <div className="text-center">
            <a
              href="/#self-host"
              className="text-sm text-slate-600 transition-colors hover:text-slate-900"
              data-testid="self-host-link"
            >
              {isMembershipDenied
                ? "Run RSL Siege Manager for your own clan →"
                : "Run RSL Siege Manager for your own clan ↗"}
            </a>
          </div>

          {/* Mobile warning — only at <768px */}
          <MobileBanner />

          {/* Privacy footer — preserved exactly */}
          <p className="text-center text-xs text-slate-500">
            We only request access to your Discord username and avatar. Guild
            membership is verified privately using our bot.
          </p>
        </div>
      </div>
    </div>
  );
}
