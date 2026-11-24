import type { Api } from "../preload/preload";

declare global {
  interface Window {
    api: Api;
  }
}

export type PageStatus = "image" | "layout" | "transcribed" | "missing";

export interface PageSummary {
  page: number;
  status: PageStatus;
  lines: number;
  has_audio: boolean;
  edited: boolean;
  words: number;
  reviewed: number;
  finished: boolean;
  image: string;
}

export interface QueueState {
  current: { path: string; page: number; message: string } | null;
  pending: { path: string; page: number }[];
}

export interface ProjectInfo {
  path: string;
  name: string;
  pages: PageSummary[];
}

export interface Word {
  id: string;
  text: string;
  boxes: number[];
  t0: number;
  t1: number;
  p: number;
  fit?: number | null;
  kind?: "1:1" | "2:1" | "1:2" | "none";
  edited?: boolean;
  accepted?: boolean;
  inserted?: boolean;
  audio?: string; // set when the word comes from a re-recorded line (relative to the page folder)
  band?: "confident" | "check" | "unsure";
  glossary?: boolean;
  glossary_span?: [number, number]; // the glossary word within the text (without prefix / punctuation)
  suggestion?: { id: string; to: string; hw?: number | null } | null;
  auto_from?: string;
  punct?: { page: string; asr: string; asr_lead?: string; status: "agree" | "none" | "page_only" | "asr_only" | "differ" | "user" };
  punct_check?: boolean; // changed automatically (language model + handwriting agreed); the original word
}

export interface Line {
  id: string;
  order: number;
  paragraph: number;
  baseline: [number, number][];
  polygons: [number, number][][];
  bbox: [number, number, number, number];
  boxes: [number, number, number, number][];
  read?: boolean;
  para_text?: number;
  skip?: boolean;
  in_drawing?: boolean;
  audio_override?: string;
  words?: Word[];
}

export type Rule = "first" | "every" | "never";

export interface GlossaryEntry {
  id: string;
  plain: string;
  pointed: string;
  rule: Rule;
  variants: string[];
  note: string;
}

export interface Settings {
  default_rule: Rule;
  check_soundalikes: boolean;
  keep_line_breaks: boolean;
  keep_asr_only_punct: boolean;
  spoken_commands: boolean;
}

export interface Suggestion {
  id: string;
  line: number;
  word: number;
  word_id: string;
  from: string;
  to: string;
  gain: number;
  before: string;
  after: string;
  status: "open" | "accepted" | "ignored";
}

export interface Page {
  page: number;
  status: PageStatus;
  size: [number, number];
  line_height?: number;
  lines: Line[];
  image: string;
  audio_path: string | null;
  audio?: { start_line: number; duration: number; discarded: [number, number][]; spaces: number[] };
  asr?: { device: string; unplaced: string[] };
  text?: string;
  suggestions?: Suggestion[];
  chapter_starts?: number[];
  drawings?: { id: string; bbox: [number, number, number, number]; source: "auto" | "user" }[];
  display_text: string;
  generated_text: string;
  edited?: boolean;
  finished?: boolean;
  reviewed_words?: number;
  review_available?: boolean;
}

export const fileUrl = (p: string, v?: string | number) =>
  "ntp://file" + p.split("/").map(encodeURIComponent).join("/") + (v !== undefined ? `?v=${v}` : "");
