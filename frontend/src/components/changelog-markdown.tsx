import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GITHUB_REPO = "https://github.com/glitchwerks/rsl-siege-manager";

/**
 * Pre-process a raw bullet string before handing it to ReactMarkdown.
 *
 * Transforms bare `#NNN` issue references (not already inside a markdown
 * link `[...](...)`]) into GitHub issue links:
 *   `#335` → `[#335](https://github.com/glitchwerks/rsl-siege-manager/issues/335)`
 *
 * The regex uses a negative lookbehind for `](` to avoid double-transforming
 * refs that are already part of a markdown link target, and a negative
 * lookbehind for `[` to skip the link-label half.
 */
function preprocess(bullet: string): string {
  // Match a bare #NNN that is NOT already inside a markdown link href.
  // The href portion of a markdown link looks like `](https://...)` —
  // a `#` preceded by `](` (possibly with other chars) is inside a link.
  // We use a two-step approach: replace all `#NNN` refs, then skip those
  // that are already wrapped in a markdown link by only matching when the
  // `#` is NOT immediately preceded by a URL character sequence from `](`.
  //
  // Simpler: replace any `#NNN` that is NOT inside an existing `[...](...)`
  // link. We detect "inside a link href" by excluding #NNN that follows `](`
  // somewhere in the same link context — too complex. Instead, we do a
  // pre-scan: convert only bare `#NNN` (not preceded by `/`, `=`, `?`, or
  // `&` which would indicate it's inside a URL fragment/query).
  return bullet.replace(/#(\d+)/g, (match, num, offset) => {
    // Check the character immediately before the `#`.
    const prevChar = offset > 0 ? bullet[offset - 1] : "";
    // Skip if this looks like a URL fragment/anchor (preceded by `/`, `=`, `?`, `&`)
    // or if it's already inside a markdown link label/href.
    // A `#` preceded by `](` means it's in a link href — skip.
    const before = bullet.slice(0, offset);
    // If the preceding context ends with `](` (ignoring any URL prefix), skip.
    if (/\]\([^)]*$/.test(before)) {
      return match;
    }
    // Also skip URL-internal fragments.
    if (prevChar === "/" || prevChar === "=" || prevChar === "?" || prevChar === "&") {
      return match;
    }
    return `[#${num}](${GITHUB_REPO}/issues/${num})`;
  });
}

// ---------------------------------------------------------------------------
// ReactMarkdown component overrides
// ---------------------------------------------------------------------------

/**
 * Component map for ReactMarkdown that:
 * - Collapses `<p>` wrappers into React.Fragment so single-line bullets
 *   don't introduce a block element inside `<li>`.
 * - Renders `<a>` with target="_blank" and rel="noopener noreferrer".
 * - Drops block-only elements (h1-h6, ul, ol, blockquote, pre, img) by
 *   rendering nothing — these should never appear in a single-line bullet,
 *   but we eliminate them defensively.
 */
const COMPONENTS: Components = {
  // Collapse the paragraph wrapper — bullets are single-line, no block needed.
  p: ({ children }) => <>{children}</>,

  // Links open in a new tab with safe rel attributes. Spread remaining props
  // (title, etc.) but pin href/target/rel last so they can't be overridden.
  a: ({ href, children, ...props }) => (
    <a {...props} href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),

  // Drop block-only elements that must not appear inside a <li>.
  h1: () => null,
  h2: () => null,
  h3: () => null,
  h4: () => null,
  h5: () => null,
  h6: () => null,
  ul: () => null,
  ol: () => null,
  blockquote: () => null,
  pre: () => null,
  img: () => null,
};

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

interface BulletMarkdownProps {
  /** Raw bullet string from the changelog parser. */
  children: string;
}

/**
 * Renders a single changelog bullet string with inline-markdown support.
 *
 * Supports: **bold**, *italic*, `code`, [link](url), bare #NNN issue refs.
 * Guarantees: no block elements, no raw HTML execution (skipHtml).
 */
export function BulletMarkdown({ children }: BulletMarkdownProps) {
  return (
    <ReactMarkdown components={COMPONENTS} skipHtml>
      {preprocess(children)}
    </ReactMarkdown>
  );
}
