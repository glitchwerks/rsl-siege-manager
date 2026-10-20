import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import type { GuideDocument as Document } from "../guides/types";

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
  const sections = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    if (!needle) return document.sections;
    return document.sections.filter((section) =>
      JSON.stringify(section).toLocaleLowerCase().includes(needle)
    );
  }, [document, search]);

  useEffect(() => {
    if (hash) window.document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);

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
      <header className="border-b border-slate-200 bg-white print:hidden">
        <nav
          aria-label="Guide navigation"
          className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-6 py-4"
        >
          <Link to="/" className="font-semibold text-slate-900">
            RSL Siege Manager
          </Link>
          <div className="flex flex-wrap items-center gap-4">
            <Link to="/guide" className="text-sm text-violet-700 underline">
              General guide
            </Link>
            <Link
              to="/sieges"
              className="rounded-md bg-violet-700 px-4 py-2 text-sm font-medium text-white hover:bg-violet-800"
            >
              Open website
            </Link>
          </div>
        </nav>
      </header>
      <main id="guide-content" className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <div className="mb-8 max-w-3xl">
          <p className="mb-3 text-sm font-semibold text-violet-700">
            {document.audience}
          </p>
          <h1 className="mb-4 text-4xl font-bold tracking-tight text-slate-900">
            {document.title}
          </h1>
          <p className="text-lg leading-relaxed">{document.intro}</p>
        </div>
        <div className="grid items-start gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="rounded-xl border border-slate-200 bg-white p-5 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto print:hidden">
            <label
              htmlFor="guide-search"
              className="mb-2 block font-semibold text-slate-900"
            >
              Find help
            </label>
            <input
              id="guide-search"
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Try reserves or Discord"
              className="mb-4 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-violet-600"
            />
            <nav aria-label="Guide topics">
              <ul className="space-y-3 text-sm">
                {sections.map(({ id, title }) => (
                  <li key={id}>
                    <a
                      onClick={() => setSearch("")}
                      className="text-violet-700 underline decoration-violet-200 underline-offset-4 hover:decoration-violet-700"
                      href={`#${id}`}
                    >
                      {title}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          </aside>
          <div className="min-w-0 space-y-8">
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
                className="scroll-mt-6 rounded-xl border border-slate-200 bg-white p-5 sm:p-8 print:break-before-page"
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
