// Record case identity from the runner events, including on Node versions
// whose JUnit reporter omits source filenames.
//
// A case is named by its suite ancestry as well as its own name ("refund > caps"), so tests that
// share a name in different `describe` blocks stay distinct. Node reports `test:start` and
// `test:pass`/`test:fail` in definition order, so the names started at each nesting level of a
// file are the ancestors of the next case that finishes deeper in it.
import { basename, resolve } from "node:path";

const SEPARATOR = " > ";

function isFile(name, file) {
  return name === file || name === basename(file) || resolve(name) === resolve(file);
}

export default async function* report(events) {
  const started = new Map(); // file -> the names started at each nesting level
  for await (const { type, data } of events) {
    if (!data?.file) continue;
    const depth = data.nesting ?? 0;
    if (type === "test:start") {
      const stack = (started.get(data.file) ?? []).slice(0, depth);
      stack[depth] = data.name;
      started.set(data.file, stack);
      continue;
    }
    if (type !== "test:pass" && type !== "test:fail") continue;
    if (data.name === data.file || data.details?.type === "suite") continue;
    // Newer Node versions emit a synthetic case at the beginning of files
    // that loaded successfully but registered no tests.
    if (data.line === 1 && data.column === 1 && isFile(data.name, data.file)) continue;
    const ancestors = (started.get(data.file) ?? [])
      .slice(0, depth)
      .filter((name) => name !== undefined && !isFile(name, data.file));
    let status = "passed";
    if (data.skip || data.todo) status = "skipped";
    else if (type === "test:fail")
      status = data.details?.error?.failureType === "testCodeFailure" ? "failed" : "error";
    const name = [...ancestors, data.name].join(SEPARATOR);
    yield JSON.stringify({ file: data.file, name, line: data.line, column: data.column, status }) + "\n";
  }
}
