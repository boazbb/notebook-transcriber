import { contextBridge, ipcRenderer } from "electron";

const api = {
  call: (method: string, params: Record<string, unknown> = {}) => ipcRenderer.invoke("helper:call", method, params),
  onEvent: (cb: (name: string, params: any) => void) => {
    const listener = (_e: unknown, name: string, params: any) => cb(name, params);
    ipcRenderer.on("helper:event", listener);
    return () => {
      ipcRenderer.removeListener("helper:event", listener);
    };
  },
  lastProject: (): Promise<string | null> => ipcRenderer.invoke("app:lastProject"),
  importPdfDialog: (): Promise<{ pdf: string; dest: string } | null> => ipcRenderer.invoke("dialog:importPdf"),
  openProjectDialog: (): Promise<string | null> => ipcRenderer.invoke("dialog:openProject"),
  audioFileDialog: (): Promise<string | null> => ipcRenderer.invoke("dialog:audioFile"),
  exportDialog: (defaultName: string): Promise<string | null> => ipcRenderer.invoke("dialog:exportTo", defaultName),
  saveRecording: (page: number, wav: ArrayBuffer, line?: number): Promise<string> =>
    ipcRenderer.invoke("recording:save", page, wav, line),
  micAccess: (): Promise<boolean> => ipcRenderer.invoke("mic:access"),
  copy: (text: string, html: string): Promise<boolean> => ipcRenderer.invoke("clipboard:write", text, html),
};

contextBridge.exposeInMainWorld("api", api);
export type Api = typeof api;
