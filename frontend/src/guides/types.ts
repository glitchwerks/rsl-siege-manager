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
}
export interface GuideDocument {
  title: string;
  intro: string;
  audience: string;
  sections: GuideSection[];
}
