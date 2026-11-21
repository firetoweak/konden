// 枚举 kondenApi 的请求形状，再由项目使用的 fetchBaseQuery 生成实际 Request。
import { readFileSync } from "node:fs";
import { fetchBaseQuery } from "@reduxjs/toolkit/query";
import ts from "typescript";

const origin = "http://konden.test";
const source = ts.createSourceFile("kondenApi.ts",
  readFileSync(new URL("../src/api/kondenApi.ts", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest, true);

function property(object, key) {
  if (!ts.isObjectLiteralExpression(object) || object.properties.some((member) =>
    ts.isSpreadAssignment(member) || ts.isComputedPropertyName(member.name))) {
    throw new Error("无法枚举请求属性：" + object.getText(source));
  }
  const member = object.properties.find((item) => item.name?.text === key);
  if (member && !ts.isPropertyAssignment(member)) {
    throw new Error("无法枚举请求属性：" + member.getText(source));
  }
  return member?.initializer;
}

function unwrapped(expression) {
  return ts.isParenthesizedExpression(expression) ? unwrapped(expression.expression) : expression;
}

function stringValues(expression) {
  const node = unwrapped(expression);
  if (ts.isStringLiteralLike(node)) return [node.text];
  if (ts.isConditionalExpression(node)) {
    return [...stringValues(node.whenTrue), ...stringValues(node.whenFalse)];
  }
  if (ts.isTemplateExpression(node)) {
    return [node.head.text + node.templateSpans.map((span) => "sample-id" + span.literal.text).join("")];
  }
  throw new Error("无法枚举请求值：" + node.getText(source));
}

function returnsOf(query) {
  if (!ts.isBlock(query.body)) return [unwrapped(query.body)];
  const returns = [];
  function visit(node) {
    if (ts.isReturnStatement(node)) returns.push(unwrapped(node.expression));
    else if (!ts.isFunctionLike(node)) ts.forEachChild(node, visit);
  }
  visit(query.body);
  return returns;
}

const declaration = source.statements.filter(ts.isVariableStatement)
  .flatMap((statement) => [...statement.declarationList.declarations])
  .find((variable) => variable.name.getText(source) === "kondenApi");
const config = declaration.initializer.arguments[0];
const baseQuery = property(config, "baseQuery");
const options = baseQuery.arguments[0];
const baseUrl = property(options, "baseUrl");
const defaultMethod = property(options, "method");
if (baseQuery.expression.getText(source) !== "fetchBaseQuery" || !ts.isStringLiteralLike(baseUrl)) {
  throw new Error("无法核对客户端的 baseQuery：" + baseQuery.getText(source));
}
const send = fetchBaseQuery({
  baseUrl: new URL(baseUrl.text, origin).href,
  fetchFn: async () => new Response(null, { status: 204 }),
});

const endpoints = unwrapped(property(config, "endpoints").body).properties;
const references = [];
for (const endpoint of endpoints) {
  const name = endpoint.name.getText(source);
  const query = property(endpoint.initializer.arguments[0], "query");
  const returns = query && returnsOf(query);
  if (!returns?.length) throw new Error("端点没有可核对的请求：" + name);
  for (const value of returns) {
    const object = ts.isObjectLiteralExpression(value);
    const method = (object ? property(value, "method") : undefined) ?? defaultMethod;
    for (const url of stringValues(object ? property(value, "url") : value)) {
      for (const verb of method ? stringValues(method) : [undefined]) {
        const { meta } = await send({ url, method: verb }, { signal: new AbortController().signal }, {});
        const target = new URL(meta.request.url);
        if (target.origin !== origin) throw new Error(name + " 指向其他主机：" + target.href);
        references.push([name, meta.request.method, target.pathname]);
      }
    }
  }
}
if (!references.length) throw new Error("没有找到客户端请求");
process.stdout.write(JSON.stringify(references));
