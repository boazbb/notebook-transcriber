// Bundle the Electron main and preload scripts (the renderer is built by Vite).
import { build } from "esbuild";
import path from "node:path";

// The packaged app runs the frozen copy of the Python helper (release/helper, updated by
// release/update_helper.sh), so work in ../helper can't affect the installed app. Environments and models
// are shared through links.
const HELPER_DIR = path.resolve("../release/helper");

const common = {
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  external: ["electron"],
  sourcemap: true,
  define: { __HELPER_DIR__: JSON.stringify(HELPER_DIR) },
};
await build({ ...common, entryPoints: ["src/main/main.ts"], outfile: "dist/main/main.js" });
await build({ ...common, entryPoints: ["src/preload/preload.ts"], outfile: "dist/preload/preload.js" });
console.log("main + preload built");
