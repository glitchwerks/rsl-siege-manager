import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GITHUB_REPO = "https://github.com/glitchwerks/rsl-siege-manager";

// Matches a full markdown link span `[label](href)` — used to carve the
// bullet into link / non-link segments so the autolinker never touches a
// ref inside a link's label OR href.
const MARKDOWN_LINK_RE = /\[[^\]]*\]\([^)]*\)/g;

// Matches a bare GitHub issue/PR reference: `#NNN`.
const BARE_REF_RE = /#(\d+)/g;

/**
 * Autolink bare `#NNN` refs within a plain-text segment (no markdown links).
 *
 * Skips refs that look like a URL fragment/anchor (preceded by `/`, `=`, `?`,
 * `&`), which can occur in a bare URL outside any `[...](...)` link.
 */
function autolinkRefs(text: string): string {
  return text.replace(BARE_REF_RE, (match, num, offset: number) => {
    const prevChar = offset > 0 ? text[offset - 1] : "";
    if (
      prevChar === "/" ||
      prevChar === "=" ||
      prevChar === "?" ||
      prevChar === "&"
    ) {
      return match;
    }
    return `[#${num}](${GITHUB_REPO}/issues/${num})`;
  });
}

/**
 * Pre-process a raw bullet string before handing it to ReactMarkdown.
 *
 * Transforms bare `#NNN` issue references into GitHub issue links:
 *   `#335` → `[#335](https://github.com/glitchwerks/rsl-siege-manager/issues/335)`
 *
 * Existing markdown links are carved out first and passed through verbatim, so
 * a ref inside a link's label (`[PR #335](…/pull/335)`) or href is never
 * rewritten — only refs in the surrounding plain text are linked.
 */
function preprocess(bullet: string): string {
  let out = "";
  let last = 0;
  MARKDOWN_LINK_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = MARKDOWN_LINK_RE.exec(bullet)) !== null) {
    // Autolink the plain-text gap before this link…
    out += autolinkRefs(bullet.slice(last, m.index));
    // …then emit the link span unchanged.
    out += m[0];
    last = m.index + m[0].length;
  }
  // Autolink the trailing plain-text segment after the last link.
  out += autolinkRefs(bullet.slice(last));
  return out;
}

// ---------------------------------------------------------------------------
// ReactMarkdown component overrides
// ---------------------------------------------------------------------------

/**
 * Component map for ReactMarkdown that:
 * - Renders the `<p>` wrapper as an inline `<span>` so the whole bullet body
 *   stays a single flex item inside the `<li>` (`flex gap-1.5`). A Fragment
 *   would make each inline node a separate flex child, inserting gaps between
 *   inline runs and letting them wrap independently.
 * - Renders `<a>` with target="_blank" and rel="noopener noreferrer".
 * - Drops block-only elements (h1-h6, ul, ol, blockquote, pre, img) by
 *   rendering nothing — these should never appear in a single-line bullet,
 *   but we eliminate them defensively.
 */
const COMPONENTS: Components = {
  // Render the paragraph wrapper as an inline span — keeps the bullet body as
  // one flex item; span is inline so it introduces no block element.
  p: ({ children }) => <span>{children}</span>,

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
