export interface GuideImage {
  src: string;
  title: string;
  alt: string;
  caption: string;
  width: number;
  height: number;
  previewCrop?: { top: number; height: number };
}

export interface GuideTable {
  colorSwatches?: Record<string, string>;
  caption: string;
  columns: string[];
  rows: string[][];
}

export interface GuideCallout {
  title: string;
  paragraphs: string[];
  images?: GuideImage[];
}

export interface GuideTask {
  callout?: GuideCallout;
  id?: string;
  tables?: GuideTable[];
  title: string;
  location?: string;
  paragraphs?: string[];
  steps?: string[];
  result?: string;
  resultTitle?: string;
  note?: string;
  images?: GuideImage[];
}
export interface GuideSection {
  id: string;
  title: string;
  summary: string;
  tasks: GuideTask[];
  images?: GuideImage[];
}
export interface GuideDocument {
  title: string;
  intro: string;
  audience: string;
  sections: GuideSection[];
}
