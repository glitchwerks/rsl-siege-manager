import { useEffect, useRef } from "react";
import type { GuideSection } from "../guides/types";

interface Props {
  sections: GuideSection[];
  activeId: string;
  search: string;
  onSearch: (value: string) => void;
  onNavigate: (id: string) => void;
  mobile?: boolean;
}

export default function GuideSidebar({
  sections,
  activeId,
  search,
  onSearch,
  onNavigate,
  mobile = false,
}: Props) {
  const navRef = useRef<HTMLElement>(null);
  const searchId = mobile ? "guide-search-mobile" : "guide-search";

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

  return (
    <div className="flex min-h-0 flex-col">
      <label
        htmlFor={searchId}
        className="mb-2 block font-semibold text-slate-900"
      >
        Find help
      </label>
      <input
        id={searchId}
        type="search"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Try reserves or Discord"
        className="mb-4 w-full shrink-0 rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-violet-600"
      />
      <nav
        ref={navRef}
        aria-label={mobile ? "Mobile guide topics" : "Guide topics"}
        className="min-h-0 overflow-y-auto overscroll-contain"
      >
        <ul className="space-y-1 pr-1 text-sm">
          {sections.map(({ id, title }) => (
            <li key={id}>
              <a
                href={`#${id}`}
                aria-current={activeId === id ? "location" : undefined}
                onClick={() => onNavigate(id)}
                className={`block rounded-md border-l-2 px-3 py-2 leading-snug transition-colors ${activeId === id ? "border-violet-600 bg-violet-50 font-semibold text-violet-900" : "border-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-950"}`}
              >
                {title}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      {sections.length === 0 && (
        <p className="text-sm text-slate-500">No matching topics.</p>
      )}
    </div>
  );
}
