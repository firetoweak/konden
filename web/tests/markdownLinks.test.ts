// @vitest-environment node
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { fromMarkdown } from "mdast-util-from-markdown";
import { visit } from "unist-util-visit";
import { expect, it } from "vitest";

const root = fileURLToPath(new URL("../../", import.meta.url));

function missingLinks(file: string, markdown: string): string[] {
  const missing: string[] = [];
  const tree = fromMarkdown(markdown);
  const definitions = new Map<string, string>();
  visit(tree, "definition", ({ identifier, url }) => {
    if (!definitions.has(identifier)) definitions.set(identifier, url);
  });
  visit(tree, (node) => {
    let href;
    if (node.type === "link" || node.type === "image") href = node.url;
    else if (node.type === "linkReference" || node.type === "imageReference") href = definitions.get(node.identifier);
    if (!href || href.startsWith("#") || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)) return;
    if (!existsSync(new URL(href, pathToFileURL(file)))) {
      missing.push(relative(root, file) + ":" + node.position!.start.line + " -> " + href);
    }
  });
  return missing;
}

it("文档的相对链接和图片指向存在的目标", () => {
  const files = ["AGENTS.md", "README.md", "review.md"].map((name) => join(root, name));
  for (const dir of ["docs", ".claude/skills"]) {
    const entries = readdirSync(join(root, dir), { recursive: true, withFileTypes: true });
    files.push(...entries.filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
      .map((entry) => join(entry.parentPath, entry.name)));
  }
  const missing = files.flatMap((file) => missingLinks(file, readFileSync(file, "utf8")));
  expect(missing).toEqual([]);
});

it("失效的引用式链接报告正文行号，跳过代码示例和未使用的定义", () => {
  const markdown = [
    "```md", "[示例](unused-example.md)", "```", "",
    "[说明][guide]", "", "[guide]: missing-guide.md", "[unused]: missing-unused.md",
  ].join("\n");
  expect(missingLinks(join(root, "README.md"), markdown)).toEqual(["README.md:5 -> missing-guide.md"]);
});
