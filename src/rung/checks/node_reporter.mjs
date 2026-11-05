// Record case identity from the runner events, including on Node versions
// whose JUnit reporter omits source filenames.
import { basename, resolve } from "node:path";

export default async function* report(events) {
  for await (const { type, data } of events) {
    if (type !== "test:pass" && type !== "test:fail") continue;
    if (!data.file || data.name === data.file || data.details?.type === "suite") continue;
    // Newer Node versions emit a synthetic case at the beginning of files
    // that loaded successfully but registered no tests.
    if (data.line === 1 && data.column === 1 &&
        (data.name === basename(data.file) || resolve(data.name) === resolve(data.file))) continue;
    let status = "passed";
    if (data.skip || data.todo) status = "skipped";
    else if (type === "test:fail")
      status = data.details?.error?.failureType === "testCodeFailure" ? "failed" : "error";
    yield JSON.stringify({ file: data.file, name: data.name, status }) + "\n";
  }
}
