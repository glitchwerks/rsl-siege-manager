import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import type { GuideDocument as Document } from "../guides/types";
import GuideSidebar from "./GuideSidebar";
import GuideScreenshot from "./GuideScreenshot";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

function Text({ children }: { children: string }) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => <p className="leading-relaxed">{children}</p>,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}

export default function GuideDocument({ document }: { document: Document }) {
  const { hash } = useLocation();
  const [search, setSearch] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeId, setActiveId] = useState(
    hash.slice(1) || document.sections[0]?.id || ""
  );
  const sections = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return document.sections;
    return document.sections.filter((section) =>
      JSON.stringify(section).toLocaleLowerCase().includes(needle)
    );
  }, [document, search]);

  useEffect(() => {
    if (hash) {
      window.document.getElementById(hash.slice(1))?.scrollIntoView();
      setActiveId(hash.slice(1));
    }
  }, [hash]);

  useEffect(() => {
    let frame = 0;
    function updateActive() {
      let current = sections[0]?.id || "";
      for (const section of sections) {
        const heading = window.document.getElementById(section.id);
        if (heading && heading.getBoundingClientRect().top <= 150)
          current = section.id;
      }
      setActiveId(current);
      frame = 0;
    }
    function onScroll() {
      if (!frame) frame = window.requestAnimationFrame(updateActive);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      window.cancelAnimationFrame(frame);
    };
  }, [sections]);

  function navigateToTopic(id: string) {
    setSearch("");
    setActiveId(id);
    setMobileOpen(false);
    // Closing a dialog restores focus and may scroll it. Jump after the next
    // render so filtering and mobile focus restoration finish first.
    window.requestAnimationFrame(() =>
      window.document.getElementById(id)?.scrollIntoView()
    );
  }

  useEffect(() => {
    const previous = window.document.title;
    window.document.title = `${document.title} — RSL Siege Manager`;
    return () => {
      window.document.title = previous;
    };
  }, [document.title]);

  return (
    <div id="guide-top" className="min-h-screen bg-slate-50 text-slate-700">
      <a
        href="#guide-content"
        className="sr-only focus:not-sr-only focus:block focus:p-4"
      >
        Skip to guide
      </a>
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white print:hidden">
        <nav
          aria-label="Guide navigation"
          className="mx-auto flex h-16 max-w-screen-2xl items-center justify-between gap-2 px-4 sm:px-6"
        >
          <Link
            to="/"
            className="text-sm font-semibold text-slate-900 sm:text-base"
          >
            RSL Siege Manager
          </Link>
          <div className="flex shrink-0 items-center gap-3">
            <Link to="/guide" className="text-sm text-violet-700 underline">
              Guide
            </Link>
            <Link
              to="/sieges"
              className="rounded-md bg-violet-700 px-3 py-2 text-sm font-medium text-white hover:bg-violet-800"
            >
              Open website
            </Link>
          </div>
        </nav>
      </header>
      <div className="sticky top-16 z-20 border-b border-slate-200 bg-white px-4 py-2 md:hidden print:hidden">
        <button
          onClick={() => setMobileOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={mobileOpen}
          className="w-full rounded-md border border-violet-200 px-3 py-2 text-left text-sm font-semibold text-violet-800"
        >
          ☰ Browse guide topics
        </button>
      </div>
      <Dialog open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogContent className="flex max-h-[85dvh] flex-col">
          <DialogHeader>
            <DialogTitle>Guide topics</DialogTitle>
            <DialogDescription>
              Search for help or choose a section.
            </DialogDescription>
          </DialogHeader>
          <GuideSidebar
            sections={sections}
            activeId={activeId}
            search={search}
            onSearch={setSearch}
            onNavigate={navigateToTopic}
            mobile
          />
        </DialogContent>
      </Dialog>
      <main
        id="guide-content"
        className="mx-auto max-w-screen-2xl px-4 py-8 sm:px-6"
      >
        <div className="grid items-start gap-6 md:grid-cols-[230px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)] print:block">
          <aside
            aria-label="Guide sidebar"
            className="sticky top-20 hidden max-h-[calc(100dvh-6rem)] flex-col rounded-xl border border-slate-200 bg-white p-4 md:flex print:hidden"
          >
            <p className="mb-4 text-xs font-semibold uppercase tracking-wider text-slate-500">
              On this guide
            </p>
            <GuideSidebar
              sections={sections}
              activeId={activeId}
              search={search}
              onSearch={setSearch}
              onNavigate={navigateToTopic}
            />
          </aside>
          <div className="min-w-0 space-y-8">
            <div className="mb-8 max-w-3xl">
              <p className="mb-3 text-sm font-semibold text-violet-700">
                {document.audience}
              </p>
              <h1 className="mb-4 text-4xl font-bold tracking-tight text-slate-900">
                {document.title}
              </h1>
              <p className="text-lg leading-relaxed">{document.intro}</p>
            </div>
            {search && (
              <p role="status">
                {sections.length} matching topic
                {sections.length === 1 ? "" : "s"}.{" "}
                <button
                  className="text-violet-700 underline"
                  onClick={() => setSearch("")}
                >
                  Show the full guide
                </button>
              </p>
            )}
            {sections.map((section) => (
              <section
                key={section.id}
                id={section.id}
                aria-labelledby={`${section.id}-title`}
                className="scroll-mt-32 rounded-xl border border-slate-200 bg-white p-5 sm:p-8 md:scroll-mt-20 print:break-before-page"
              >
                <h2
                  id={`${section.id}-title`}
                  className="text-2xl font-bold text-slate-900"
                >
                  {section.title}
                </h2>
                <p className="mt-3 leading-relaxed text-slate-500">
                  {section.summary}
                </p>
                {section.images?.map((image) => (
                  <GuideScreenshot key={image.src} image={image} />
                ))}
                <div className="mt-6 divide-y divide-slate-100">
                  {section.tasks.map((task) => (
                    <div key={task.title} className="space-y-4 py-6 first:pt-0">
                      <h3 className="text-lg font-semibold text-slate-900">
                        {task.title}
                      </h3>
                      {task.location && (
                        <p className="rounded-md bg-slate-100 px-3 py-2 text-sm font-medium text-slate-800">
                          Where to go: {task.location}
                        </p>
                      )}
                      {task.paragraphs?.map((paragraph) => (
                        <Text key={paragraph}>{paragraph}</Text>
                      ))}
                      {task.steps && (
                        <ol className="list-decimal space-y-3 pl-6 marker:font-semibold marker:text-violet-700">
                          {task.steps.map((step) => (
                            <li key={step}>
                              <Text>{step}</Text>
                            </li>
                          ))}
                        </ol>
                      )}
                      {task.result && (
                        <div className="rounded-lg border-l-4 border-green-600 bg-green-50 p-4 text-green-950">
                          <p className="mb-1 text-sm font-semibold">
                            What you should see
                          </p>
                          <Text>{task.result}</Text>
                        </div>
                      )}
                      {task.note && (
                        <div className="rounded-lg bg-violet-50 p-4 text-violet-950">
                          <Text>{task.note}</Text>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <a
                  href="#guide-top"
                  className="mt-4 inline-block text-sm text-violet-700 underline print:hidden"
                >
                  Back to topics ↑
                </a>
              </section>
            ))}
            {sections.length === 0 && (
              <p>
                No topics match that search. Try a button name or a shorter
                phrase.
              </p>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
