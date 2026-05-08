import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });

// Start at leaderboard.
await page.goto("http://localhost:3001/", { waitUntil: "networkidle" });
await page.waitForTimeout(500);

// Type a random ticker into the nav search and submit.
await page.locator('input[placeholder="TICKER"]').fill("nflx");
await page.keyboard.press("Enter");
await page.waitForURL("**/tickers/NFLX**");
await page.waitForTimeout(8000);

await page.screenshot({
  path: "/Users/jaspervalk/Documents/projects/trading-signal-research/_design/_search-nflx.png",
  fullPage: true,
});
console.log("ok url=", page.url());
await browser.close();
