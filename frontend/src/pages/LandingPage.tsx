import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import Carousel, { type CarouselSlide } from "../components/Carousel";

// ---------------------------------------------------------------------------
// LandingOrSieges — Decision 6.2 from the plan doc.
// Wraps the landing page so authenticated users are silently redirected to
// /sieges. Keeps LandingPage itself pure (no auth knowledge needed there).
// ---------------------------------------------------------------------------
export function LandingOrSieges() {
  const { isAuthenticated, isLoading } = useAuth();

  // While the auth check is in flight, show nothing to avoid flash.
  if (isLoading) return null;

  if (isAuthenticated) {
    return <Navigate to="/sieges" replace />;
  }

  return <LandingPage />;
}

// ---------------------------------------------------------------------------
// Workflow carousel slides
// ---------------------------------------------------------------------------
const SLIDES: CarouselSlide[] = [
  {
    image: "/landing/carousel-assignment-board.png",
    placeholder: "Assignment board",
    title: "Assignment board",
    description: "Drag-and-drop member buckets per building",
  },
  {
    image: "/landing/carousel-member-management.png",
    placeholder: "Member management",
    title: "Member management",
    description: "Roster with roles, scroll counts, and Discord sync",
  },
  {
    image: "/landing/carousel-validation-errors.png",
    placeholder: "Validation errors",
    title: "Validation errors",
    description: "16 rules enforced live as you edit",
  },
  {
    image: "/landing/carousel-siege-comparison.png",
    placeholder: "Siege comparison",
    title: "Siege comparison",
    description: "Side-by-side planning view",
  },
  {
    image: "/landing/carousel-post-assignments.png",
    placeholder: "Post assignments",
    title: "Post assignments",
    description: "Assign members to post buildings for each siege",
  },
  {
    image: "/landing/carousel-discord-image.png",
    placeholder: "Generated Discord image",
    title: "Generated Discord image",
    description: "The PNG posted to your channel",
  },
];

// ---------------------------------------------------------------------------
// Inline SVG helpers (Lucide-style, no import needed for a public page)
// ---------------------------------------------------------------------------
function ShieldIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-violet-600"
      aria-hidden="true"
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      className="mt-0.5 shrink-0 text-violet-600"
      xmlns="http://www.w3.org/2000/svg"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function ExternalLinkIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}

// Discord brand blue — not expressible as a standard Tailwind class.
const COLORS = {
  discordBlue: "#5865F2",
} as const;

// ---------------------------------------------------------------------------
// LandingPage
// ---------------------------------------------------------------------------
export default function LandingPage() {
  function handleHeroCta(e: React.MouseEvent<HTMLAnchorElement>) {
    e.preventDefault();
    document
      .getElementById("self-host")
      ?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <>
      {/* ------------------------------------------------------------------ *
       *  SEO meta tags                                                        *
       * ------------------------------------------------------------------ */}
      {/* Note: vite-plugin-react-ssg is tracked in the issue acceptance       *
       *  criteria but is not installed yet. Meta tags are inlined here so     *
       *  they appear in the pre-rendered HTML once SSG is wired up.           *
       *  For now they are rendered client-side.                               */}

      {/* ------------------------------------------------------------------ *
       *  STICKY NAV                                                           *
       * ------------------------------------------------------------------ */}
      <nav className="sticky top-0 z-50 border-b border-slate-200 bg-white/95 backdrop-blur transition-all duration-200">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          {/* Logo + wordmark */}
          <a
            href="/"
            className="flex items-center gap-2 text-slate-900 transition-colors hover:text-violet-700"
          >
            <ShieldIcon />
            <span className="text-base font-semibold tracking-tight">
              RSL Siege Manager
            </span>
          </a>

          {/* GitHub icon + Sign in */}
          <div className="flex items-center gap-3">
            <a href="/guide" className="text-sm font-medium text-violet-700 hover:underline">
              Usage guide
            </a>
            <a
              href="https://github.com/glitchwerks/rsl-siege-manager"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
              aria-label="View on GitHub"
            >
              <GitHubIcon />
            </a>
            <a
              href="/login"
              className="rounded-md px-4 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
              style={{ backgroundColor: COLORS.discordBlue }}
            >
              Sign in
            </a>
          </div>
        </div>
      </nav>

      <main>
        {/* ---------------------------------------------------------------- *
         *  HERO                                                               *
         * ---------------------------------------------------------------- */}
        <section
          className="flex min-h-[82vh] items-center"
          style={{
            backgroundColor: "#ffffff",
            backgroundImage:
              "radial-gradient(circle at 60% 20%, rgba(124, 58, 237, 0.06) 0%, transparent 60%), radial-gradient(#e2e8f0 1px, transparent 1px)",
            backgroundSize: "100% 100%, 24px 24px",
          }}
        >
          <div className="mx-auto max-w-4xl px-6 py-24 text-center">
            <p className="mb-5 text-xs font-semibold uppercase tracking-widest text-slate-500">
              A portfolio project by{" "}
              <a href="https://christopherbeaulieu.net" target="_blank" rel="noopener noreferrer" className="text-violet-600 hover:underline">Christopher Beaulieu</a>
            </p>
            <h1 className="mb-4 text-4xl font-bold leading-tight tracking-tight text-slate-900 sm:text-5xl">
              A siege assignment tool I built
              <br className="hidden sm:block" /> for my Raid: Shadow Legends
              clan.
            </h1>
            <p className="mb-6 text-sm tracking-wide text-slate-500">
              FastAPI · React · PostgreSQL · Azure Container Apps · Discord bot
            </p>
            <p className="mx-auto mb-10 max-w-2xl text-lg leading-relaxed text-slate-700">
              Fully open source. I built it for my own clan, but everything you
              need to run it for yours is in the repo.
            </p>
            <a
              href="#self-host"
              onClick={handleHeroCta}
              className="inline-block rounded-lg bg-violet-600 px-8 py-3.5 text-base font-semibold text-white shadow-sm transition-colors hover:bg-violet-700"
              data-testid="hero-cta"
            >
              Set it up for your clan ↓
            </a>
          </div>
        </section>

        {/* ---------------------------------------------------------------- *
         *  WHAT IT DOES                                                       *
         * ---------------------------------------------------------------- */}
        <section id="features" className="bg-slate-50 py-24">
          <div className="mx-auto max-w-6xl px-6">
            <h2 className="mb-12 text-3xl font-bold text-slate-900">
              What it does
            </h2>
            <div className="grid items-start gap-10 md:grid-cols-2">
              {/* Hero board screenshot */}
              <img
                src="/landing/hero-board.png"
                alt="Siege assignment board showing member positions across buildings"
                className="h-96 w-full rounded-lg border border-slate-200 object-cover shadow-sm"
                data-testid="board-screenshot"
              />

              {/* Feature bullets */}
              <ul className="space-y-4" data-testid="feature-list">
                {[
                  "Drag-and-drop member bucket per building",
                  "Member management with roles, scroll counts, and Discord sync",
                  "Post condition and member preference system",
                  "Building-level slot awareness with support for broken buildings",
                  "Auto-fill algorithm that respects pin state and attack-day thresholds",
                  "16 validation rules enforced live as you edit",
                  "Side-by-side siege comparison for planning",
                  "Generated PNG boards posted to Discord automatically",
                  "Per-member DM notifications with delivery tracking",
                ].map((text) => (
                  <li key={text} className="flex items-start gap-3">
                    <CheckIcon />
                    <span className="text-slate-700">{text}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Carousel */}
            <Carousel slides={SLIDES} />
          </div>
        </section>

        {/* ---------------------------------------------------------------- *
         *  UNDER THE HOOD                                                     *
         * ---------------------------------------------------------------- */}
        <section id="architecture" className="bg-white py-24">
          <div className="mx-auto max-w-6xl px-6">
            <h2 className="mb-4 text-3xl font-bold text-slate-900">
              Under the hood
            </h2>
            <p className="mb-12 max-w-2xl leading-relaxed text-slate-600">
              Three containerized services deployed via Bicep
              infrastructure-as-code, with CI/CD through GitHub Actions.
              Cloud-agnostic by design — the whole stack runs anywhere Docker
              runs.
            </p>

            {/* Service cards */}
            <div className="mb-8 grid gap-6 md:grid-cols-3">
              {[
                {
                  name: "siege-api",
                  desc: "FastAPI · SQLAlchemy async · Playwright for image generation",
                },
                {
                  name: "siege-frontend",
                  desc: "React 18 · Vite · TypeScript · Tailwind · shadcn/ui",
                },
                {
                  name: "siege-bot",
                  desc: "discord.py client + FastAPI HTTP sidecar",
                },
              ].map(({ name, desc }) => (
                <div
                  key={name}
                  className="rounded-lg border border-slate-200 p-6 shadow-sm transition-shadow hover:shadow-md"
                >
                  <p className="mb-2 font-mono text-sm font-semibold text-violet-700">
                    {name}
                  </p>
                  <p className="text-sm leading-relaxed text-slate-600">
                    {desc}
                  </p>
                </div>
              ))}
            </div>

            <p className="mb-4 text-center text-sm text-slate-500">
              PostgreSQL · Key Vault · Application Insights · Log Analytics
            </p>

            <div className="text-center">
              <a
                href="https://github.com/glitchwerks/rsl-siege-manager"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900"
              >
                View architecture on GitHub
                <ExternalLinkIcon />
              </a>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- *
         *  SELF-HOST                                                          *
         * ---------------------------------------------------------------- */}
        <section id="self-host" className="bg-slate-50 py-24">
          <div className="mx-auto max-w-6xl px-6">
            <h2 className="mb-4 text-3xl font-bold text-slate-900">
              Run it for your own clan
            </h2>
            <p className="mb-12 max-w-2xl leading-relaxed text-slate-600">
              RSL Siege Manager is fully open source and deliberately
              cloud-agnostic. Three paths — pick the one that fits your setup.
            </p>

            <div className="mb-8 grid gap-6 lg:grid-cols-3">
              {/* Card 1: Try locally */}
              <div className="flex flex-col rounded-xl border border-slate-200 bg-white p-6 transition-shadow hover:shadow-md">
                <div className="mb-3 text-3xl">👀</div>
                <h3 className="mb-0.5 font-semibold text-slate-900">
                  Try it locally
                </h3>
                <p className="mb-3 text-xs font-medium text-slate-400">
                  ~5 minutes
                </p>
                <p className="mb-6 flex-1 text-sm leading-relaxed text-slate-600">
                  Docker Compose + auth disabled + seed data. Click around the
                  UI with zero Discord setup.
                </p>
                <a
                  href="https://github.com/glitchwerks/rsl-siege-manager#quick-start"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-md border border-slate-300 px-4 py-2 text-center text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
                  data-testid="self-host-local-link"
                >
                  Try it ↗
                </a>
              </div>

              {/* Card 2: Self-host anywhere (recommended) */}
              <div className="flex flex-col rounded-xl border-2 border-violet-400 bg-white p-6 ring-2 ring-violet-100 transition-shadow hover:shadow-md">
                <div className="mb-3 text-3xl">🏠</div>
                <div className="mb-0.5 flex items-center gap-2">
                  <h3 className="font-semibold text-slate-900">
                    Self-host anywhere
                  </h3>
                  <span className="rounded-full bg-violet-100 px-2 py-0.5 text-xs font-semibold text-violet-700">
                    Recommended
                  </span>
                </div>
                <p className="mb-3 text-xs font-medium text-slate-400">
                  ~30 minutes
                </p>
                <p className="mb-6 flex-1 text-sm leading-relaxed text-slate-600">
                  VPS or home server + Docker. Works on any box that runs
                  containers. No cloud bill.
                </p>
                <a
                  href="https://github.com/glitchwerks/rsl-siege-manager/wiki/Self-Host-on-Any-VPS"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-md border border-violet-400 px-4 py-2 text-center text-sm font-medium text-violet-700 transition-colors hover:bg-violet-50"
                  data-testid="self-host-anywhere-link"
                >
                  Self-host guide ↗
                </a>
              </div>

              {/* Card 3: Azure */}
              <div className="flex flex-col rounded-xl border border-slate-200 bg-white p-6 transition-shadow hover:shadow-md">
                <div className="mb-3 text-3xl">☁</div>
                <h3 className="mb-0.5 font-semibold text-slate-900">
                  Deploy to Azure
                </h3>
                <p className="mb-3 text-xs font-medium text-slate-400">
                  ~1–2 hours
                </p>
                <p className="mb-6 flex-1 text-sm leading-relaxed text-slate-600">
                  Bicep templates, Container Apps, Key Vault, CI/CD. Managed
                  hands-off infrastructure.
                </p>
                <a
                  href="https://github.com/glitchwerks/rsl-siege-manager/wiki/Self-Host-on-Azure"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block rounded-md border border-slate-300 px-4 py-2 text-center text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
                  data-testid="self-host-azure-link"
                >
                  Azure guide ↗
                </a>
              </div>
            </div>

            <p className="text-center text-sm text-slate-500">
              All three use the same stack. Start small, graduate later if you
              ever want to.
            </p>
          </div>
        </section>

        {/* ---------------------------------------------------------------- *
         *  CONTACT + FOOTER                                                   *
         * ---------------------------------------------------------------- */}
        <section id="contact" className="bg-white py-24">
          <div className="mx-auto max-w-6xl px-6">
            <h2 className="mb-8 text-3xl font-bold text-slate-900">Contact</h2>
            <div className="mb-8 space-y-3">
              <p className="text-slate-700">
                <span className="inline-block w-20 font-medium text-slate-400">
                  Discord
                </span>
                higgsbp
              </p>
              <p className="text-slate-700">
                <span className="inline-block w-20 font-medium text-slate-400">
                  LinkedIn
                </span>
                <a
                  href="https://www.linkedin.com/in/christopher-beaulieu/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-violet-600 hover:underline"
                >
                  linkedin.com/in/christopher-beaulieu
                </a>
              </p>
              <p className="text-slate-700">
                <span className="inline-block w-20 font-medium text-slate-400">
                  GitHub
                </span>
                <a
                  href="https://github.com/glitchwerks/rsl-siege-manager"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-violet-600 hover:underline"
                >
                  github.com/glitchwerks/rsl-siege-manager
                </a>
              </p>
            </div>

            <p className="mb-10 inline-block rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800">
              ⚠ The app itself isn&rsquo;t optimized for mobile. For the best
              experience, use a desktop browser.
            </p>

            <div className="border-t border-slate-200 pt-6">
              <p className="text-sm text-slate-400">
                © 2026 Christopher Beaulieu · Built as a portfolio project
              </p>
            </div>
          </div>
        </section>
      </main>
    </>
  );
}
