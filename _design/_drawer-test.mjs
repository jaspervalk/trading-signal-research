import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
await page.goto("http://localhost:3001/tickers/AAPL", { waitUntil: "networkidle" });
await page.waitForTimeout(3000);
// open the first claim drawer
await page.locator("details.group").first().locator("summary").click();
await page.waitForTimeout(500);
await page.screenshot({
  path: "/Users/jaspervalk/Documents/projects/trading-signal-research/_design/_live-aapl-drawer.png",
  fullPage: true,
});
await browser.close();
console.log("done");
