import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders Sophia's dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Sophia · Desktop Companion<\/title>/i);
  assert.match(html, /Desktop Companion/i);
  assert.match(html, /Put her to sleep/i);
  assert.match(html, /THE TUBE/i);
  assert.match(html, /LONG-TERM MEMORY/i);
  assert.match(html, /SOPHIA’S SOUNDBOARD/i);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/i);
});

test("keeps the dashboard source and metadata Sophia-specific", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(layout, /Sophia · Desktop Companion/);
  assert.match(page, /className="shell"/);
  assert.match(page, /id="the-tube"/);
  assert.match(page, /Game-event awareness/);
  assert.match(page, /What Sophia remembers/);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(packageJson, /WRANGLER_LOG_PATH=/);
});
