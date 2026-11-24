import { app, BrowserWindow, clipboard, dialog, ipcMain, net, protocol, session, systemPreferences } from "electron";
import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { pathToFileURL } from "node:url";
import readline from "node:readline";

declare const __HELPER_DIR__: string; // set at build time (build.mjs): this repository's helper folder
// The Python helper (and its environments and models) stays in the repository, also for the packaged app.
const HELPER_DIR = process.env.NT_HELPER_DIR ?? (app.isPackaged ? __HELPER_DIR__ : path.resolve(__dirname, "../../../helper"));
const ICON = path.resolve(__dirname, "../../assets/icon.png");
const STATE_FILE = () => path.join(app.getPath("userData"), "state.json");

// Files (page images, recordings) are served to the window through ntp://file/<absolute path>,
// limited to the open project folder.
protocol.registerSchemesAsPrivileged([
  { scheme: "ntp", privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } },
]);

// NT_FAKE_MIC=1: Chromium's fake microphone (a test tone) instead of the real one (testing)
if (process.env.NT_FAKE_MIC) {
  app.commandLine.appendSwitch("use-fake-device-for-media-stream");
  app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
}

let win: BrowserWindow | null = null;
let currentProject: string | null = null;

// ---------- helper process (Python, JSON-RPC over stdio) ----------

let helper: ChildProcessWithoutNullStreams | null = null;
let nextId = 1;
const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

function findUv(): string {
  const candidates = [path.join(os.homedir(), ".local/bin/uv"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"];
  return candidates.find((c) => existsSync(c)) ?? "uv";
}

function startHelper() {
  if (!existsSync(path.join(HELPER_DIR, "notebook_helper"))) {
    dialog.showErrorBox(
      "Notebook Transcriber",
      `The helper folder wasn't found at\n${HELPER_DIR}\n\nThe app uses the Python helper and models in the project folder; keep it there or set NT_HELPER_DIR.`,
    );
    return;
  }
  // the frozen copy (packaged app) runs with its environment's Python directly, so uv never syncs or
  // changes anything while the app is in use; development runs through uv
  const venvPython = path.join(HELPER_DIR, ".venv", "bin", "python");
  const [cmd, args] = app.isPackaged && existsSync(venvPython)
    ? [venvPython, ["-m", "notebook_helper"]]
    : [findUv(), ["run", "--quiet", "python", "-m", "notebook_helper"]];
  helper = spawn(cmd, args, {
    cwd: HELPER_DIR,
    env: { ...process.env, HF_HUB_OFFLINE: "1", PYTHONUNBUFFERED: "1" },
  });
  readline.createInterface({ input: helper.stdout }).on("line", (line) => {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    if (msg.id !== undefined) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (!p) return;
      if (msg.error !== undefined) p.reject(new Error(msg.error));
      else p.resolve(msg.result);
    } else if (msg.event) {
      win?.webContents.send("helper:event", msg.event, msg.params);
    }
  });
  helper.stderr.on("data", (d) => {
    const s = d.toString();
    if (!s.includes("objc[")) console.error("[helper]", s.trim());
  });
  helper.on("exit", (code) => {
    for (const p of pending.values()) p.reject(new Error("The helper process stopped."));
    pending.clear();
    win?.webContents.send("helper:event", "log", { level: "error", message: `Helper stopped (code ${code}). Restarting…` });
    helper = null;
    setTimeout(startHelper, 1000);
  });
}

function callHelper(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (!helper) return reject(new Error("The helper process is not running."));
    const id = nextId++;
    pending.set(id, { resolve, reject });
    helper.stdin.write(JSON.stringify({ id, method, params }) + "\n");
  });
}

// ---------- remembered state ----------

function loadState(): { lastProject?: string } {
  try {
    return JSON.parse(readFileSync(STATE_FILE(), "utf-8"));
  } catch {
    return {};
  }
}

function saveState(s: { lastProject?: string }) {
  try {
    mkdirSync(path.dirname(STATE_FILE()), { recursive: true });
    writeFileSync(STATE_FILE(), JSON.stringify(s));
  } catch {
    /* not critical */
  }
}

// ---------- IPC ----------

function setProject(p: string | null) {
  currentProject = p;
  if (p && !process.env.NT_PROJECT) saveState({ lastProject: p }); // test runs don't change the remembered project
}

ipcMain.handle("helper:call", async (_e, method: string, params: Record<string, unknown>) => {
  const result: any = await callHelper(method, params);
  if ((method === "open_project" || method === "create_project") && result?.path) setProject(result.path);
  return result;
});

ipcMain.handle("app:lastProject", () => {
  const p = process.env.NT_PROJECT ?? loadState().lastProject; // NT_PROJECT: open this project (testing)
  return p && existsSync(path.join(p, "project.json")) ? p : null;
});

ipcMain.handle("dialog:importPdf", async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: "Import a scanned notebook (PDF)",
    filters: [{ name: "PDF", extensions: ["pdf"] }],
    properties: ["openFile"],
  });
  if (r.canceled || !r.filePaths[0]) return null;
  const pdf = r.filePaths[0];
  const s = await dialog.showSaveDialog(win!, {
    title: "Where should the project folder go?",
    defaultPath: path.join(path.dirname(pdf), path.basename(pdf, ".pdf") + ".ntproj"),
    buttonLabel: "Create project",
  });
  if (s.canceled || !s.filePath) return null;
  const dest = s.filePath.endsWith(".ntproj") ? s.filePath : s.filePath + ".ntproj";
  return { pdf, dest };
});

ipcMain.handle("dialog:openProject", async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: "Open a notebook project (.ntproj folder)",
    properties: ["openDirectory", "treatPackageAsDirectory"],
  });
  if (r.canceled || !r.filePaths[0]) return null;
  return r.filePaths[0];
});

ipcMain.handle("dialog:audioFile", async () => {
  const r = await dialog.showOpenDialog(win!, {
    title: "Use an existing recording for this page",
    filters: [{ name: "Audio", extensions: ["m4a", "mp3", "wav", "aac", "ogg", "flac", "webm", "mp4"] }],
    properties: ["openFile"],
  });
  return r.canceled ? null : r.filePaths[0] ?? null;
});

ipcMain.handle("dialog:exportTo", async (_e, defaultName: string) => {
  const s = await dialog.showSaveDialog(win!, {
    title: "Export text",
    defaultPath: path.join(currentProject ? path.join(currentProject, "export") : app.getPath("documents"), defaultName),
    filters: [{ name: "Text", extensions: ["txt"] }],
  });
  return s.canceled ? null : s.filePath ?? null;
});

ipcMain.handle("recording:save", async (_e, page: number, wav: ArrayBuffer, line?: number) => {
  if (!currentProject) throw new Error("No project open.");
  const dest = (line
    ? await callHelper("line_audio_path", { path: currentProject, page, line })
    : await callHelper("audio_path", { path: currentProject, page })) as string;
  // keep the previous recording and words in the page's history before anything is replaced
  await callHelper("archive_page", {
    path: currentProject,
    page,
    reason: line ? `before re-recording line ${line}` : "before recording the page again",
  });
  mkdirSync(path.dirname(dest), { recursive: true });
  writeFileSync(dest, Buffer.from(wav));
  return dest;
});

// plain text plus HTML with right-to-left paragraphs (Google Docs keeps the direction and alignment)
ipcMain.handle("clipboard:write", (_e, text: string, html: string) => {
  clipboard.write({ text, html });
  return true;
});

ipcMain.handle("mic:access", async () => {
  if (process.platform !== "darwin" || process.env.NT_FAKE_MIC) return true;
  if (systemPreferences.getMediaAccessStatus("microphone") === "granted") return true;
  return systemPreferences.askForMediaAccess("microphone");
});

// ---------- window ----------

function createWindow() {
  win = new BrowserWindow({
    width: 1500,
    height: 950,
    title: "Notebook Transcriber",
    icon: ICON,
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, "../renderer/index.html"));
  win.on("closed", () => (win = null));
  // NT_EXEC=<file.js>: run a test script in the window once it has loaded (testing)
  if (process.env.NT_EXEC) {
    const js = readFileSync(process.env.NT_EXEC, "utf-8");
    win.webContents.once("did-finish-load", () => {
      win?.webContents.executeJavaScript(js).catch((e) => console.error("[test]", e));
    });
  }
  win.webContents.on("console-message", (_e, _level, message) => {
    if (message.startsWith("[test]")) console.log(message);
  });
  // NT_SCREENSHOT=<file.png>:<seconds> saves a screenshot of the window after a delay (testing)
  const shot = process.env.NT_SCREENSHOT;
  if (shot) {
    const [file, secs] = shot.split(":");
    setTimeout(async () => {
      const img = await win?.webContents.capturePage();
      if (img) writeFileSync(file, img.toPNG());
      if (process.env.NT_QUIT_AFTER_SHOT) app.quit();
    }, Number(secs ?? 8) * 1000);
  }
}

app.whenReady().then(() => {
  protocol.handle("ntp", (req) => {
    const file = decodeURIComponent(new URL(req.url).pathname);
    if (!currentProject || !path.resolve(file).startsWith(path.resolve(currentProject) + path.sep)) {
      return new Response("forbidden", { status: 403 });
    }
    return net.fetch(pathToFileURL(file).toString());
  });
  if (process.platform === "darwin" && existsSync(ICON)) app.dock?.setIcon(ICON);
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === "media"));
  startHelper();
  createWindow();
});

app.on("window-all-closed", () => {
  helper?.kill();
  app.quit();
});
