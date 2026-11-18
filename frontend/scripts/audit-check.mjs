// 画面に入る依存（dependencies）の既知の脆弱性を調べ、high 以上があれば止める（CI の「依存関係の既知の脆弱性」。
// 2026-11-17 の決定: 止める）。npm audit には見送りの一覧が無いので、この道具が audit-ignore.txt に書いた勧告を外す。
//
//   node scripts/audit-check.mjs                 … frontend で（npm run audit:check と同じ）
//   node scripts/audit-check.mjs <一覧のファイル> … 別の一覧で試す
//
// 終了コード: 0 = 止める脆弱性なし、1 = あり（または一覧の書き方の誤り）、2 = npm audit を動かせない・結果を読めない
// 開発用の道具（devDependencies）は配布物に入らないので見ない（--omit=dev）。依存のパッケージは入れない（Node だけで動く）。
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

const LEVELS = ["info", "low", "moderate", "high", "critical"];
const THRESHOLD = "high";
const ID_PATTERN = /^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$/;
const ignoreFile = process.argv[2] ?? "audit-ignore.txt";

/** 見送りの一覧を読む: 1 行に 1 つ「GHSA-xxxx-xxxx-xxxx  # 理由」。# で始まる行と空行は飛ばす */
function readIgnoreList(path) {
  const ignored = new Map();
  const problems = [];
  readFileSync(path, "utf8").split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) return;
    const [id, ...rest] = line.split("#");
    const advisory = id.trim();
    const reason = rest.join("#").trim();
    if (!ID_PATTERN.test(advisory)) {
      problems.push(`${path}:${index + 1}: 勧告の ID は GHSA-xxxx-xxxx-xxxx の形で書く（いま: "${advisory}"）`);
    } else if (reason === "") {
      problems.push(`${path}:${index + 1}: ${advisory} に理由が無い（# のあとに、見送る理由・見直す日・担当を書く）`);
    } else {
      ignored.set(advisory, reason);
    }
  });
  return { ignored, problems };
}

/** npm audit の結果から、勧告ごとに 1 つ（同じ勧告が複数のパッケージに出ても 1 つにまとめる） */
function advisoriesOf(report) {
  const byId = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vulnerability.via) {
      if (typeof via !== "object") continue; // 文字列 = 別のパッケージの勧告を経由しているだけ
      const id = String(via.url ?? "").split("/").pop() || `npm-${via.source}`;
      const found = byId.get(id) ?? { id, title: via.title, severity: via.severity, url: via.url, packages: new Set() };
      found.packages.add(`${via.name} ${via.range}`);
      byId.set(id, found);
    }
  }
  return [...byId.values()];
}

const { ignored, problems } = readIgnoreList(ignoreFile);
if (problems.length > 0) {
  problems.forEach((p) => console.error(`::error::${p}`));
  process.exit(1);
}

// 決まった 1 つの命令の文字列（外からの値を混ぜない）。Windows の npm は npm.cmd なのでシェル経由で動かす
const audit = spawnSync("npm audit --omit=dev --json", { encoding: "utf8", shell: true, maxBuffer: 64 * 1024 * 1024 });
let report;
try {
  report = JSON.parse(audit.stdout);
} catch {
  console.error(`::error::npm audit の結果を読めない（終了コード ${audit.status}）\n${audit.stderr}`);
  process.exit(2);
}
if (report.error) {
  console.error(`::error::npm audit が失敗した: ${report.error.summary ?? JSON.stringify(report.error)}`);
  process.exit(2);
}

const advisories = advisoriesOf(report);
const serious = advisories.filter((a) => LEVELS.indexOf(a.severity) >= LEVELS.indexOf(THRESHOLD));
const blocking = serious.filter((a) => !ignored.has(a.id));
const skipped = serious.filter((a) => ignored.has(a.id));
const stale = [...ignored.keys()].filter((id) => !advisories.some((a) => a.id === id));
const below = advisories.length - serious.length;

const show = (a) => `  ${a.id} [${a.severity}] ${a.title}\n    ${[...a.packages].join(", ")}\n    ${a.url}`;
if (skipped.length > 0) {
  console.log(`見送り（${ignoreFile} の理由つき）: ${skipped.length} 件`);
  skipped.forEach((a) => console.log(`${show(a)}\n    理由: ${ignored.get(a.id)}`));
}
if (stale.length > 0) {
  // 直った・取り下げられた勧告の行は残しておく意味がない（止めはしないが、消すように知らせる）
  stale.forEach((id) => console.log(`::warning::${ignoreFile} の ${id} はもう出ていない。行を消す`));
}
if (below > 0) {
  console.log(`${THRESHOLD} 未満の勧告: ${below} 件（止めない。npm audit --omit=dev で見られる）`);
}
if (blocking.length > 0) {
  console.error(`::error::${THRESHOLD} 以上の既知の脆弱性: ${blocking.length} 件（package.json の版を修正版に上げて npm install）`);
  blocking.forEach((a) => console.error(show(a)));
  process.exit(1);
}
console.log(`${THRESHOLD} 以上の既知の脆弱性なし（画面に入る依存）`);
