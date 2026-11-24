// Plays stretches of a page's recordings (a word with padding, or a whole line) with Web Audio.
// Each file (the page take, re-recorded lines) is decoded once and kept, so seeking is instant.

export class StretchPlayer {
  private ctx: AudioContext | null = null;
  private buffers = new Map<string, Promise<AudioBuffer>>();
  private source: AudioBufferSourceNode | null = null;
  private current: string | null = null;

  load(url: string): Promise<AudioBuffer> {
    this.ctx ??= new AudioContext();
    const base = url.split("?")[0];
    for (const k of [...this.buffers.keys()]) if (k.split("?")[0] === base && k !== url) this.buffers.delete(k);
    let b = this.buffers.get(url);
    if (!b) {
      b = fetch(url)
        .then((r) => r.arrayBuffer())
        .then((d) => this.ctx!.decodeAudioData(d));
      b.catch(() => this.buffers.delete(url));
      this.buffers.set(url, b);
    }
    this.current = url;
    return b;
  }

  async play(t0: number, t1: number, padding = 0.3, url?: string) {
    const u = url ?? this.current;
    if (!u) return;
    const buffer = await this.load(u);
    this.stop();
    const start = Math.max(0, t0 - padding);
    const end = Math.min(buffer.duration, t1 + padding);
    if (end <= start || !this.ctx) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    src.start(0, start, end - start);
    this.source = src;
  }

  stop() {
    try {
      this.source?.stop();
    } catch {
      /* already stopped */
    }
    this.source = null;
  }
}
