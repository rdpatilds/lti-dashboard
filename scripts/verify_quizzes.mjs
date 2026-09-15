import { chromium } from "playwright";

const BASE_URL = (process.env.CANVAS_URL || "http://localhost:3100").replace(/\/$/, "");
const TIMEOUT_MS = 120000;

function parseArgs(argv) {
  const args = { course: "1" };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--login") args.login = argv[++i];
    else if (flag === "--password") args.password = argv[++i];
    else if (flag === "--quiz") args.quiz = argv[++i];
    else if (flag === "--expect") args.expect = argv[++i];
    else if (flag === "--out") args.out = argv[++i];
    else if (flag === "--open") args.open = argv[++i];
    else if (flag === "--course") args.course = argv[++i];
    else throw new Error("unrecognized argument: " + flag);
  }
  if (!args.login || !args.password || !args.quiz || !args.expect || !args.out) {
    throw new Error("--login, --password, --quiz, --expect present|absent, and --out are required; --open <quiz url> optional");
  }
  return args;
}

async function logIn(page, login, password) {
  for (let attempt = 1; attempt <= 4; attempt++) {
    await page.goto(`${BASE_URL}/login/canvas`, { waitUntil: "domcontentloaded" });
    if (await page.$("#pseudonym_session_unique_id")) break;
  }
  await page.fill("#pseudonym_session_unique_id", login);
  await page.fill("#pseudonym_session_password", password);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }), page.click("#login_form input[type='submit']")]);
}

const args = parseArgs(process.argv.slice(2));
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.setDefaultTimeout(TIMEOUT_MS);
await logIn(page, args.login, args.password);
await page.goto(`${BASE_URL}/courses/${args.course}/quizzes`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(6000);
const text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
const present = text.includes(args.quiz);
await page.screenshot({ path: args.out, fullPage: true });
const ok = args.expect === "present" ? present : !present;
console.log(`${ok ? "PASS" : "FAIL"} quizzes page for ${args.login}: "${args.quiz}" ${present ? "listed" : "not listed"}, expected ${args.expect}; ${args.out}`);
let openOk = true;
if (args.open) {
  await page.goto(args.open, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(6000);
  const quizText = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
  const outOpen = args.out.replace(/\.png$/, "") + "-quiz.png";
  await page.screenshot({ path: outOpen, fullPage: true });
  openOk = quizText.includes(args.quiz);
  console.log(`${openOk ? "PASS" : "FAIL"} quiz page ${args.open}: title ${openOk ? "shown" : "missing"}; ${outOpen}`);
}
await browser.close();
process.exit(ok && openOk ? 0 : 1);
