// One command to run every check: `npm test` from the /tests folder.
// 1) Lambda logic + template checks (Python, no AWS needed)  2) CloudFormation lint (if installed)  3) Browser tests (Chrome)
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const steps = [
  ['Lambda logic and template checks', 'py', ['-m', 'unittest', 'discover', '-s', 'backend']],
  ['Browser tests (Chrome + mocked AWS)', process.execPath, ['--test', 'e2e/app.test.js']],
];
const lint = path.join(root, '.venv', 'Scripts', 'cfn-lint.exe');
if (fs.existsSync(lint)) steps.splice(1, 0, ['CloudFormation lint (cfn-lint)', lint, ['../recircuit-template.yaml']]);

let failed = 0;
for (const [name, cmd, args] of steps) {
  console.log(`\n=== ${name} ===`);
  const r = spawnSync(cmd, args, { cwd: root, stdio: 'inherit' });
  if (r.status !== 0) { failed++; console.log(`FAILED: ${name}`); }
}
console.log(failed ? `\n${failed} step(s) failed` : '\nAll checks passed');
process.exit(failed ? 1 : 0);
