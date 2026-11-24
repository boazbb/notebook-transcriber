// One continuous take per page: raw 16 kHz mono PCM via an AudioWorklet, encoded as WAV on stop.
// Marks (Space = line end, Backspace = discard since the last mark) use the audio clock:
// the number of samples captured so far, so they can't drift from the recording.

const SR = 16000;

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);
`;

export class Recorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private chunks: Float32Array[] = [];
  private samples = 0;
  private lastMark = 0;
  spaces: number[] = [];
  discarded: [number, number][] = [];
  level = 0;

  get seconds() {
    return this.samples / SR;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    this.ctx = new AudioContext({ sampleRate: SR });
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "capture");
    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => {
      const d = e.data;
      this.chunks.push(d);
      this.samples += d.length;
      let peak = 0;
      for (let i = 0; i < d.length; i++) peak = Math.max(peak, Math.abs(d[i]));
      this.level = Math.max(peak, this.level * 0.9);
    };
    src.connect(this.node);
    this.chunks = [];
    this.samples = 0;
    this.lastMark = 0;
    this.spaces = [];
    this.discarded = [];
  }

  markLineEnd() {
    const t = this.seconds;
    this.spaces.push(round(t));
    this.lastMark = t;
  }

  discardSinceMark() {
    const t = this.seconds;
    if (t > this.lastMark) this.discarded.push([round(this.lastMark), round(t)]);
    this.lastMark = t;
  }

  async stop(): Promise<ArrayBuffer> {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = null;
    const pcm = new Float32Array(this.samples);
    let o = 0;
    for (const c of this.chunks) {
      pcm.set(c, o);
      o += c.length;
    }
    this.chunks = [];
    return encodeWav(pcm);
  }
}

function round(t: number) {
  return Math.round(t * 100) / 100;
}

function encodeWav(pcm: Float32Array): ArrayBuffer {
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + pcm.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, SR, true);
  v.setUint32(28, SR * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}
