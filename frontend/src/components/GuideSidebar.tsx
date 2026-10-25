import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { GuideSection } from "../guides/types";
import type { GuidePageDefinition } from "../guides/pages";

interface Props {
  pages: GuidePageDefinition[];
  currentPath: string;
  sections: GuideSection[];
  activeId: string;
  search: string;
  onSearch: (value: string) => void;
  onNavigate: (id?: string) => void;
}

export default function GuideSidebar({
  pages,
  currentPath,
  sections,
  activeId,
  search,
  onSearch,
  onNavigate,
}: Props) {
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navRef.current;
    const current = nav?.querySelector<HTMLElement>(
      '[aria-current="location"]'
    );
    if (!nav || !current) return;
    const linkRect = current.getBoundingClientRect();
    const navRect = nav.getBoundingClientRect();
    if (linkRect.top < navRect.top) nav.scrollTop -= navRect.top - linkRect.top;
    else if (linkRect.bottom > navRect.bottom)
      nav.scrollTop += linkRect.bottom - navRect.bottom;
  }, [activeId]);

  function renderPage(page: GuidePageDefinition) {
    const children = pages.filter((child) => child.parentHref === page.href);
    return (
      <li key={page.href}>
        <Link
          to={page.href}
          aria-current={page.href === currentPath ? "page" : undefined}
          onClick={() => onNavigate()}
          className={`block rounded-md px-3 py-2 font-semibold ${page.href === currentPath ? "bg-violet-100 text-violet-950" : "text-slate-700 hover:bg-slate-100"}`}
        >
          {page.title}
        </Link>
        {page.href === currentPath && (
          <ul className="ml-3 mt-1 space-y-1 border-l border-slate-200">
            {sections.map(({ id, title }) => (
              <li key={id}>
                <Link
                  to={`${currentPath}#${id}`}
                  aria-current={activeId === id ? "location" : undefined}
                  onClick={() => onNavigate(id)}
                  className={`block rounded-r-md border-l-2 px-3 py-2 leading-snug ${activeId === id ? "border-violet-600 bg-violet-50 font-semibold text-violet-900" : "border-transparent text-slate-600 hover:bg-slate-100"}`}
                >
                  {title}
                </Link>
              </li>
            ))}
          </ul>
        )}
        {children.length > 0 && (
          <ul
            aria-label={`${page.title} subpages`}
            className="ml-3 mt-2 space-y-1 border-l border-slate-200 pl-2"
          >
            {children.map(renderPage)}
          </ul>
        )}
      </li>
    );
  }

  return (
    <div id="guide-navigation-content" className="flex min-h-0 flex-col">
      <label
        htmlFor="guide-search"
        className="mb-2 block text-sm font-semibold text-slate-900"
      >
        Find help on this page
      </label>
      <input
        id="guide-search"
        type="search"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Search this page"
        className="mb-4 w-full shrink-0 rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-violet-600"
      />
      <nav
        ref={navRef}
        aria-label="Guide topics"
        className="min-h-0 overflow-y-auto overscroll-contain"
      >
        <ul className="space-y-3 pr-1 text-sm">
          {pages.filter((page) => !page.parentHref).map(renderPage)}
        </ul>
      </nav>
      {sections.length === 0 && (
        <p className="mt-3 text-sm text-slate-500">
          No matching topics on this page.
        </p>
      )}
    </div>
  );
}
