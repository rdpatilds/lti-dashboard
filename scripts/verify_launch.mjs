import { chromium } from "playwright";
import fs from "fs";

const BASE_URL = (process.env.CANVAS_URL || "http://localhost:3100").replace(/\/$/, "");
const TOOL_ORIGIN = (process.env.TOOL_URL || "http://localhost:8800").replace(/\/$/, "").replace(/^https?:\/\//, "");
const NAV_TIMEOUT_MS = 30000;
const FRAME_TIMEOUT_MS = 30000;
const CONTENT_TIMEOUT_MS = 60000;
const POLL_INTERVAL_MS = 500;
const COURSE_NAV_LABEL = "Performance Dashboard";
const CONTENT_TERMS = ["HISTORY", "LIVE"];
const ROLE_TERMS = { student: ["Elena Rossi", "recommendations", "Your path"], teacher: ["All students", "PATH"] };

const checks = [];
function record(name, ok, detail) { checks.push({ name, ok, detail }); return ok; }

function parseArgs(argv) {
  const args = { course: "1" };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--login") args.login = argv[++i];
    else if (flag === "--password") args.password = argv[++i];
    else if (flag === "--expect") args.expect = argv[++i];
    else if (flag === "--out") args.out = argv[++i];
    else if (flag === "--course") args.course = argv[++i];
    else throw new Error("unrecognized argument: " + flag);
  }
  if (!args.login || !args.password || !args.expect || !args.out) {
    throw new Error("--login, --password, --expect, and --out are required");
  }
  if (args.expect !== "student" && args.expect !== "teacher") {
    throw new Error("--expect must be student or teacher");
  }
  return args;
}

async function logIn(page, login, password) {
  await page.goto(`${BASE_URL}/login/canvas`, { waitUntil: "domcontentloaded" });
  await page.fill("#pseudonym_session_unique_id", login);
  await page.fill("#pseudonym_session_password", password);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }), page.click("#login_form input[type='submit']")]);
  const termsCheckbox = await page.$('input[name="user[terms_of_use]"]');
  if (termsCheckbox) {
    await page.check('input[name="user[terms_of_use]"]');
    await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded" }), page.click('button[type="submit"], input[type="submit"]')]);
  }
}

function attachLogging(page, errors) {
  // Every frame navigation is printed rather than filtered: the tool iframe's redirect chain is
  // the one that matters and it cannot be identified until after it has navigated.
  page.on("framenavigated", (frame) => console.log(`nav: ${frame.url()}`));
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes(TOOL_ORIGIN) || url.includes("/api/lti/")) console.log(`req: ${request.method()} ${url}`);
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) errors.push(message.text());
  });
}

async function frameText(frame) {
  return frame.evaluate(() => (document.body ? document.body.innerText : "")).catch(() => "");
}

function missingTerms(text, terms) {
  return terms.filter((term) => !text.includes(term));
}

async function waitForTerms(frame, terms, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const text = await frameText(frame);
    if (missingTerms(text, terms).length === 0) return { matched: true, text };
    if (Date.now() >= deadline) return { matched: false, text };
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
}

async function findLtiFrame(page) {
  for (const frame of page.frames()) {
    if (frame.url().includes("lti")) return { frame, how: "page.frames() url contains lti" };
  }
  for (const element of await page.$$("iframe")) {
    const src = await element.getAttribute("src");
    if (src && src.includes("lti")) {
      const frame = await element.contentFrame();
      if (frame) return { frame, how: `iframe src contains lti: ${src}` };
    }
  }
  return null;
}

async function stepLogin(page, args) {
  await logIn(page, args.login, args.password);
  return `${args.login} at ${BASE_URL}`;
}

async function stepCoursePage(page, args) {
  await page.goto(`${BASE_URL}/courses/${args.course}`, { waitUntil: "domcontentloaded" });
  return page.url();
}

async function stepCourseNavLink(page, args, state) {
  await page.waitForSelector("#section-tabs a", { timeout: NAV_TIMEOUT_MS }).catch(() => null);
  const hrefs = await page.evaluate((label) => {
    const found = [];
    for (const link of document.querySelectorAll("#section-tabs a")) {
      if (link.textContent.trim() === label) found.push(link.getAttribute("href"));
    }
    return found;
  }, COURSE_NAV_LABEL);
  if (hrefs.length === 0) throw new Error(`no course nav link with text "${COURSE_NAV_LABEL}"`);
  state.courseNavHref = hrefs[0];
  return hrefs.length === 1 ? hrefs[0] : `${hrefs[0]} (launching the first of ${hrefs.length} entries with this label: ${hrefs.join(", ")})`;
}

async function stepNavClick(page, args, state) {
  const visible = await page.locator(`#section-tabs a[href="${state.courseNavHref}"]`).isVisible();
  // The link step already proved the placement is in the nav. Navigating to its href avoids a
  // click racing the dev Canvas, which serves one request at a time; teachers also get the
  // course nav collapsed behind the hamburger, so the link can be hidden.
  await page.goto(`${BASE_URL}${state.courseNavHref}`, { waitUntil: "domcontentloaded", timeout: CONTENT_TIMEOUT_MS });
  return `${page.url()} (link ${visible ? "visible" : "hidden"}, navigated to href)`;
}

async function settledUrl(frame, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (frame.url() === "about:blank" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return frame.url();
}

async function stepToolFrame(page, args, state) {
  const element = await page.waitForSelector("iframe.tool_launch, #tool_content", { timeout: FRAME_TIMEOUT_MS }).catch(() => null);
  if (element) {
    const frame = await element.contentFrame();
    if (frame) {
      state.frame = frame;
      return `iframe.tool_launch / #tool_content: ${await settledUrl(frame, FRAME_TIMEOUT_MS)}`;
    }
  }
  const fallback = await findLtiFrame(page);
  if (!fallback) throw new Error("no LTI iframe found by selector or by lti url/src");
  state.frame = fallback.frame;
  return `${fallback.how}: ${await settledUrl(fallback.frame, FRAME_TIMEOUT_MS)}`;
}

async function stepFrameContent(page, args, state) {
  const { matched, text } = await waitForTerms(state.frame, CONTENT_TERMS, CONTENT_TIMEOUT_MS);
  state.frameText = text;
  if (!matched) throw new Error(`missing ${missingTerms(text, CONTENT_TERMS).join(", ")} after ${CONTENT_TIMEOUT_MS} ms, ${text.length} chars of frame text`);
  return CONTENT_TERMS.join(", ");
}

async function stepRoleContent(page, args, state) {
  const terms = ROLE_TERMS[args.expect];
  const { matched, text } = await waitForTerms(state.frame, terms, CONTENT_TIMEOUT_MS);
  state.frameText = text;
  if (!matched) throw new Error(`${args.expect} frame text missing ${missingTerms(text, terms).join(", ")} after ${CONTENT_TIMEOUT_MS} ms`);
  return `${args.expect}: ${terms.join(", ")}`;
}

async function stepNextStepLinks(page, args, state) {
  const links = await state.frame.$$eval('a[target="_top"]', (anchors) =>
    anchors.map((a) => `${a.textContent.trim()} => ${a.getAttribute("href")}`));
  if (links.length === 0) throw new Error("no next-step anchors in the frame");
  return `${links.length} anchors: ${links.slice(0, 3).join(" | ")}`;
}

async function stepScreenshot(page, args) {
  await page.screenshot({ path: args.out, fullPage: true });
  return args.out;
}

async function stepScreenshotScrolled(page, args, state) {
  // The launch iframe is 800px wide, so the rightmost columns of a wide table sit off screen.
  const scrolled = await state.frame.$$eval(".scroll", (boxes) => boxes.map((b) => (b.scrollLeft = b.scrollWidth)).length);
  const outPath = args.out.replace(/\.png$/, "") + "-right.png";
  await page.screenshot({ path: outPath, fullPage: true });
  return `${outPath} (${scrolled} scroll boxes)`;
}

async function stepFrameTextFile(page, args, state) {
  const text = state.frameText === undefined ? await frameText(state.frame) : state.frameText;
  const outPath = `${args.out}.txt`;
  fs.writeFileSync(outPath, text);
  return `${outPath}, ${text.length} chars`;
}

async function stepGlobalNav(page, args, state) {
  await page.waitForSelector("header#header #menu, header#header #global_nav_tools_list", { timeout: NAV_TIMEOUT_MS }).catch(() => null);
  // Matched on text rather than id: the LTI global nav entry renders with a generated id.
  const found = await page.evaluate((label) => {
    const roots = document.querySelectorAll("header#header #menu, header#header #global_nav_tools_list, #menu, #global_nav_tools_list");
    for (const root of roots) {
      for (const el of root.querySelectorAll("a, button")) {
        if (el.textContent.replace(/\s+/g, " ").trim().includes(label)) return { href: el.getAttribute("href"), tag: el.tagName.toLowerCase() };
      }
    }
    return null;
  }, COURSE_NAV_LABEL);
  if (!found) throw new Error(`absent: no global nav link or button containing "${COURSE_NAV_LABEL}"`);
  state.globalNavHref = found.href;
  return `present: ${found.tag} href ${found.href || "(none)"}`;
}

async function stepGlobalNavLaunch(page, args, state) {
  await page.goto(`${BASE_URL}${state.globalNavHref}`, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
  const element = await page.waitForSelector("iframe.tool_launch, #tool_content", { timeout: FRAME_TIMEOUT_MS });
  const frame = await element.contentFrame();
  await settledUrl(frame, FRAME_TIMEOUT_MS);
  const { matched, text } = await waitForTerms(frame, CONTENT_TERMS, CONTENT_TIMEOUT_MS);
  if (!matched) throw new Error(`global launch missing ${missingTerms(text, CONTENT_TERMS).join(", ")}`);
  const heading = text.split(String.fromCharCode(10)).find((line) => line.includes("course")) || "";
  return `global navigation launch rendered, ${heading.trim() || "no course heading"}`;
}

const STEPS = [
  { name: "login", run: stepLogin },
  { name: "course page", run: stepCoursePage, needs: "login" },
  { name: "course nav link", run: stepCourseNavLink, needs: "course page" },
  { name: "nav click", run: stepNavClick, needs: "course nav link" },
  { name: "tool frame", run: stepToolFrame, needs: "nav click" },
  { name: "frame content", run: stepFrameContent, needs: "tool frame" },
  { name: "role content", run: stepRoleContent, needs: "frame content" },
  { name: "next step links", run: stepNextStepLinks, needs: "frame content" },
  { name: "screenshot", run: stepScreenshot, needs: "login" },
  { name: "screenshot scrolled right", run: stepScreenshotScrolled, needs: "tool frame" },
  { name: "frame text file", run: stepFrameTextFile, needs: "tool frame" },
  { name: "global nav entry", run: stepGlobalNav, needs: "login" },
  { name: "global nav launch", run: stepGlobalNavLaunch, needs: "global nav entry" },
];

async function runSteps(page, args) {
  const state = {};
  const passed = new Set();
  for (const step of STEPS) {
    if (step.needs && !passed.has(step.needs)) {
      record(step.name, false, `precondition "${step.needs}" failed`);
      continue;
    }
    try {
      record(step.name, true, await step.run(page, args, state));
      passed.add(step.name);
    } catch (error) {
      record(step.name, false, error.message || String(error));
    }
  }
}

function printReport(errors) {
  for (const check of checks) console.log(`${check.ok ? "PASS" : "FAIL"} ${check.name}  ${check.detail}`);
  const failed = checks.filter((check) => !check.ok).length;
  console.log(`${checks.length - failed}/${checks.length} checks passed, ${failed} failed`);
  console.log("page and console errors:");
  if (errors.length === 0) console.log("  (none)");
  for (const error of errors) console.log(`  ${error}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.setDefaultTimeout(120000);

  const errors = [];
  attachLogging(page, errors);

  try {
    await runSteps(page, args);
  } finally {
    await browser.close();
  }

  printReport(errors);
  process.exit(checks.every((check) => check.ok) ? 0 : 1);
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
