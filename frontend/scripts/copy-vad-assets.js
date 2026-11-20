const fs = require("fs");
const path = require("path");

const publicDir = path.join(__dirname, "..", "public");
if (!fs.existsSync(publicDir)) fs.mkdirSync(publicDir, { recursive: true });

try {
  const vadPkg = path.dirname(require.resolve("@ricky0123/vad-web/package.json"));
  const vadDist = path.join(vadPkg, "dist");

  fs.copyFileSync(
    path.join(vadDist, "vad.worklet.bundle.min.js"),
    path.join(publicDir, "vad.worklet.bundle.min.js")
  );
  fs.copyFileSync(
    path.join(vadDist, "silero_vad_v5.onnx"),
    path.join(publicDir, "silero_vad_v5.onnx")
  );
  console.log("VAD assets copied to public/");
} catch (e) {
  console.warn("Could not copy VAD assets:", e.message);
}
