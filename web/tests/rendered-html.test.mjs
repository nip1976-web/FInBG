import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

test("the dashboard is wired to the FinBG API", async () => {
  const [page, layout] = await Promise.all([
    readFile(new URL("app/page.tsx", projectRoot), "utf8"),
    readFile(new URL("app/layout.tsx", projectRoot), "utf8"),
  ]);

  assert.match(page, /\/api\/dashboard\/summary/);
  assert.match(page, /<Sidebar current="\/"/);
  assert.match(layout, /lang="ru"/);
  assert.match(layout, /favicon\.svg/);
  assert.doesNotMatch(page, /SkeletonPreview|Your site is taking shape/);
});

test("the shared sidebar exposes every implemented route", async () => {
  const sidebar = await readFile(
    new URL("app/Sidebar.tsx", projectRoot),
    "utf8",
  );

  assert.match(sidebar, /from "next\/link"/);
  assert.match(sidebar, /href: "\/"/);
  assert.match(sidebar, /href: "\/data"/);
  assert.match(sidebar, /href: "\/imports"/);
  assert.doesNotMatch(sidebar, /<a\b/);
});

test("obsolete starter-preview files are absent", async () => {
  await assert.rejects(
    access(new URL("app/_sites-preview/SkeletonPreview.tsx", projectRoot)),
  );
  await assert.rejects(
    access(new URL("app/_sites-preview/preview.css", projectRoot)),
  );
});
