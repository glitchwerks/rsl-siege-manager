import { describe, expect, it } from "vitest";
import { guideSectionText } from "./search";
import type { GuideImage, GuideSection } from "./types";

const image: GuideImage = {
  src: "/hidden-asset.jpg", title: "Screenshot title", alt: "Accessible description",
  caption: "Image caption", width: 1234, height: 5678,
  previewCrop: { top: 91, height: 92 },
};
const section: GuideSection = {
  id: "hidden-anchor", title: "Section title", summary: "Section summary", images: [image],
  tasks: [{
    id: "hidden-task", title: "Task title", location: "Task location",
    paragraphs: ["Task paragraph"], steps: ["Task step"], resultTitle: "Result title",
    result: "Task result", note: "Task note", images: [image],
    tables: [{ caption: "Table caption", columns: ["Column name"], rows: [["Cell value"]],
      colorSwatches: { red: "hidden-css-class" } }],
    callout: { title: "Callout title", paragraphs: ["Callout paragraph"], images: [image] },
  }],
};

describe("guide search", () => {
  it("includes readable instructions, tables, callouts and image descriptions", () => {
    const text = guideSectionText(section);
    for (const phrase of ["Section title", "Section summary", "Task title", "Task location",
      "Task paragraph", "Task step", "Result title", "Task result", "Task note",
      "Table caption", "Column name", "Cell value", "Callout title", "Callout paragraph",
      "Screenshot title", "Accessible description", "Image caption"])
      expect(text).toContain(phrase);
  });
  it("excludes identifiers, asset paths, styles, dimensions and object keys", () => {
    const text = guideSectionText(section);
    for (const phrase of ["hidden-", "1234", "5678", "previewCrop", "colorSwatches"])
      expect(text).not.toContain(phrase);
  });
});
