export interface GuideImage {
  src: string;
  title: string;
  alt: string;
  caption: string;
  width: number;
  height: number;
}

export interface GuideTask {
  title: string;
  location?: string;
  paragraphs?: string[];
  steps?: string[];
  result?: string;
  note?: string;
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
