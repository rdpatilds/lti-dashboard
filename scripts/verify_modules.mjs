import { chromium } from "playwright";

const BASE_URL = (process.env.CANVAS_URL || "http://localhost:3100").replace(/\/$/, "");
const TIMEOUT_MS = 120000;

function parseArgs(argv) {
  const args = { course: "1" };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--login") args.login = argv[++i];
    else if (flag === "--password") args.password = argv[++i];
    else if (flag === "--module") args.module = argv[++i];
    else if (flag === "--expect") args.expect = argv[++i];
    else if (flag === "--out") args.out = argv[++i];
    else if (flag === "--course") args.course = argv[++i];
    else throw new Error("unrecognized argument: " + flag);
  }
  if (!args.login || !args.password || !args.module || !args.expect || !args.out) {
    throw new Error("--login, --password, --module, --expect present|absent, and --out are required");
  }
  return args;
}

async function logIn(page, login, password) {
  // The dev Canvas sometimes answers the login page with a 500; reload until the form is there.
  for (let attempt = 1; attempt <= 4; attempt++) {
    await page.goto(`${BASE_URL}/login/canvas`, { waitUntil: "domcontentloaded", timeout: TIMEOUT_MS });
    if (await page.$("#pseudonym_session_unique_id")) break;
    console.log(`login form missing on attempt ${attempt}, retrying`);
  }
  await page.fill("#pseudonym_session_unique_id", login);
  await page.fill("#pseudonym_session_password", password);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: TIMEOUT_MS }),
    page.click("#login_form input[type='submit']"),
  ]);
}

const args = parseArgs(process.argv.slice(2));
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.setDefaultTimeout(120000);
await logIn(page, args.login, args.password);
await page.goto(`${BASE_URL}/courses/${args.course}/modules`, { waitUntil: "domcontentloaded", timeout: TIMEOUT_MS });
await page.waitForSelector("#context_modules", { timeout: TIMEOUT_MS });
await page.waitForTimeout(5000);
const names = await page.$$eval("#context_modules .context_module, #context_modules [data-module-id]", (els) =>
  els.map((el) => (el.querySelector("h2, .ig-header-title, .name, [data-testid='module-header-title']")?.textContent || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim()).filter(Boolean));
if (names.length === 0) {
  const fallback = await page.$$eval("h2, [role='heading']", (els) => els.map((el) => el.textContent.replace(/\s+/g, " ").trim()));
  names.push(...fallback);
}
const present = names.some((n) => n.includes(args.module));
await page.screenshot({ path: args.out, fullPage: true });
const ok = args.expect === "present" ? present : !present;
console.log(`modules seen by ${args.login}: ${JSON.stringify(names)}`);
console.log(`${ok ? "PASS" : "FAIL"} "${args.module}" ${present ? "present" : "absent"}, expected ${args.expect}; screenshot ${args.out}`);
await browser.close();
process.exit(ok ? 0 : 1);
