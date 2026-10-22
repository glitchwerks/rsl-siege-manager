import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import type { GuideDocument as Document } from "../guides/types";
import GuideSidebar from "./GuideSidebar";
import GuideScreenshot from "./GuideScreenshot";
import { guidePages } from "../guides/pages";
import { guideSectionText } from "../guides/search";

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
  const { hash, pathname } = useLocation();
  const [searchState, setSearchState] = useState({ path: pathname, query: "" });
  const search = searchState.path === pathname ? searchState.query : "";
  const setSearch = (query: string) =>
    setSearchState({ path: pathname, query });
  const [expanded, setExpanded] = useState(() => {
    try {
      const saved = localStorage.getItem("guide-navigation-expanded");
      if (saved !== null) return saved === "true";
    } catch {
      /* Navigation still works when browser storage is unavailable. */
    }
    return window.matchMedia
      ? window.matchMedia("(min-width: 768px)").matches
      : true;
  });
  const pages = guidePages.some((page) => page.href === pathname)
    ? guidePages
    : [...guidePages, { ...document, href: pathname }];
  const pageIndex = guidePages.findIndex((page) => page.href === pathname);
  function toggleNavigation() {
    setExpanded((value) => {
      try {
        localStorage.setItem("guide-navigation-expanded", String(!value));
      } catch {
        /* Optional preference. */
      }
      return !value;
    });
  }
  const hashSectionId = document.sections.find(
    (section) =>
      section.id === hash.slice(1) ||
      section.tasks.some((task) => task.id === hash.slice(1))
  )?.id;
  const [activeId, setActiveId] = useState(
    hashSectionId || document.sections[0]?.id || ""
  );
  const sections = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return document.sections;
    return document.sections.filter((section) =>
      guideSectionText(section).toLocaleLowerCase().includes(needle)
    );
  }, [document, search]);

  useEffect(() => {
    if (hash) {
      window.document.getElementById(hash.slice(1))?.scrollIntoView?.();
      setActiveId(hashSectionId || document.sections[0]?.id || "");
    } else {
      window.document.getElementById("guide-top")?.scrollIntoView?.();
      setActiveId(document.sections[0]?.id || "");
    }
  }, [hash, pathname, document, hashSectionId]);

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

  function navigateToTopic(id?: string) {
    setSearch("");
    if (id) setActiveId(id);
    if (window.matchMedia?.("(max-width: 767px)").matches) setExpanded(false);
    window.requestAnimationFrame(() =>
      window.document.getElementById(id || "guide-top")?.scrollIntoView?.()
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
      <main
        id="guide-content"
        className="mx-auto max-w-screen-2xl px-4 py-8 sm:px-6"
      >
        <div
          className={`grid items-start gap-6 print:block ${expanded ? "md:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[300px_minmax(0,1fr)]" : "md:grid-cols-[64px_minmax(0,1fr)]"}`}
        >
          <aside
            aria-label="Guide sidebar"
            className="sticky top-20 z-20 flex max-h-[calc(100dvh-6rem)] flex-col rounded-xl border border-slate-200 bg-white p-3 print:hidden"
          >
            <button
              type="button"
              aria-label={
                expanded
                  ? "Minimize guide navigation"
                  : "Expand guide navigation"
              }
              aria-expanded={expanded}
              aria-controls="guide-navigation-content"
              onClick={toggleNavigation}
              className={`flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-violet-800 hover:bg-violet-50 ${expanded ? "mb-3 justify-between" : "justify-center"}`}
            >
              <span className={expanded ? "" : "md:sr-only"}>
                Guide navigation
              </span>
              <span aria-hidden="true">{expanded ? "‹" : "☰"}</span>
            </button>
            {expanded && (
              <GuideSidebar
                pages={pages}
                currentPath={pathname}
                sections={sections}
                activeId={activeId}
                search={search}
                onSearch={setSearch}
                onNavigate={navigateToTopic}
              />
            )}
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
                  Show all topics on this page
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
                    <div
                      key={task.title}
                      id={task.id}
                      className="scroll-mt-24 space-y-4 py-6 first:pt-0"
                    >
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
                      {task.images?.map((image) => (
                        <GuideScreenshot key={image.src} image={image} />
                      ))}
                      {task.tables?.map((table) => (
                        <div
                          key={table.caption}
                          role="region"
                          aria-label={table.caption}
                          tabIndex={0}
                          className="overflow-x-auto rounded-lg border border-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-600"
                        >
                          <table className="w-full border-collapse text-center text-sm">
                            <caption className="bg-white px-4 py-3 text-left font-semibold text-slate-900">
                              {table.caption}
                            </caption>
                            <thead className="bg-slate-100 text-slate-800">
                              <tr>
                                {table.columns.map((column) => (
                                  <th
                                    key={column}
                                    scope="col"
                                    className="border-t border-slate-200 px-2 py-2"
                                  >
                                    {column}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {table.rows.map((row, rowIndex) => (
                                <tr
                                  key={row[0]}
                                  className={
                                    rowIndex === 0 && !table.colorSwatches
                                      ? "bg-violet-50"
                                      : "bg-white"
                                  }
                                >
                                  {row.map((cell, columnIndex) =>
                                    columnIndex === 0 ? (
                                      <th
                                        key={columnIndex}
                                        scope="row"
                                        className="border-t border-slate-200 px-3 py-2 font-semibold text-slate-900"
                                      >
                                        {cell}
                                      </th>
                                    ) : (
                                      <td
                                        key={columnIndex}
                                        className="border-t border-slate-200 px-3 py-2"
                                      >
                                        {table.colorSwatches?.[cell] ? (
                                          <span className="inline-flex items-center gap-2">
                                            <span
                                              aria-hidden="true"
                                              className={`h-4 w-4 shrink-0 rounded-sm ${table.colorSwatches[cell]}`}
                                            />
                                            {cell}
                                          </span>
                                        ) : (
                                          cell
                                        )}
                                      </td>
                                    )
                                  )}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
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
                      {task.callout && (
                        <aside
                          aria-label={task.callout.title}
                          className="space-y-4 rounded-xl border border-l-4 border-amber-300 border-l-amber-500 bg-amber-50 p-5 text-amber-950 sm:p-6"
                        >
                          <h4 className="text-lg font-bold">
                            {task.callout.title}
                          </h4>
                          {task.callout.paragraphs.map((paragraph) => (
                            <Text key={paragraph}>{paragraph}</Text>
                          ))}
                          {task.callout.images?.map((image) => (
                            <GuideScreenshot key={image.src} image={image} />
                          ))}
                        </aside>
                      )}
                      {task.result && (
                        <div className="rounded-lg border-l-4 border-green-600 bg-green-50 p-4 text-green-950">
                          <p className="mb-1 text-sm font-semibold">
                            {task.resultTitle || "What you should see"}
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
                No topics match that search on this page. Try a button name or a
                shorter phrase.
              </p>
            )}
            {pageIndex >= 0 && (
              <nav
                aria-label="Guide page navigation"
                className="flex flex-wrap justify-between gap-4 border-t border-slate-200 pt-6 print:hidden"
              >
                {pageIndex > 0 ? (
                  <Link
                    to={guidePages[pageIndex - 1].href}
                    className="rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm font-semibold text-violet-800"
                  >
                    ← {guidePages[pageIndex - 1].title}
                  </Link>
                ) : (
                  <span />
                )}
                {pageIndex < guidePages.length - 1 && (
                  <Link
                    to={guidePages[pageIndex + 1].href}
                    className="rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm font-semibold text-violet-800"
                  >
                    {guidePages[pageIndex + 1].title} →
                  </Link>
                )}
              </nav>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
