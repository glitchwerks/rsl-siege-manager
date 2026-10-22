import type { GuideImage, GuideSection } from "./types";

function imageText(images: GuideImage[] = []): string[] {
  return images.flatMap(({ title, alt, caption }) => [title, alt, caption]);
}

/** Search reader-facing text without matching asset paths or presentation metadata. */
export function guideSectionText(section: GuideSection): string {
  return [
    section.title,
    section.summary,
    ...imageText(section.images),
    ...section.tasks.flatMap((task) => [
      task.title,
      task.location,
      ...(task.paragraphs ?? []),
      ...(task.steps ?? []),
      task.resultTitle,
      task.result,
      task.note,
      ...imageText(task.images),
      ...(task.tables ?? []).flatMap((table) => [
        table.caption,
        ...table.columns,
        ...table.rows.flat(),
      ]),
      task.callout?.title,
      ...(task.callout?.paragraphs ?? []),
      ...imageText(task.callout?.images),
    ]),
  ].filter(Boolean).join("\n");
}
