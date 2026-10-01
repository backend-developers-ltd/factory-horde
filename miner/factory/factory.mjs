#!/usr/bin/env node
/** Non-inference baseline: verify Pi, wait one minute, publish a fresh greeting project. */
import { createHash, randomUUID } from 'node:crypto';
import { constants, openSync, readFileSync, writeFileSync, fsyncSync, closeSync, renameSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { setTimeout } from 'node:timers/promises';

const specification = readFileSync('/input/specification.md');
const manifest = JSON.parse(readFileSync('/input/task.json', 'utf8'));
if (manifest.protocol_version !== 1 ||
    manifest.specification_sha256 !== createHash('sha256').update(specification).digest('hex')) {
  throw new Error('Input manifest/specification mismatch');
}

// An explicit environment prevents accidental model credentials or user extensions.
const result = spawnSync('/opt/pi/node_modules/.bin/pi', ['--version'], {
  env: { PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp', PI_CODING_AGENT_DIR: '/tmp/pi', NO_COLOR: '1' },
  encoding: 'utf8',
  timeout: 15000,
  maxBuffer: 65536,
});
if (result.error || result.status !== 0 || result.stdout.trim() !== '0.87.1') {
  throw new Error(`Pi version check failed: ${result.error?.message ?? result.stderr}`);
}
console.log(JSON.stringify({ event: 'pi_version_verified', version: result.stdout.trim(), inference: false }));
await setTimeout(60000);

for (const [name, text] of [
  ['main.py', 'print("Hello from FactoryHorde!")\n'],
  ['README.md', '# FactoryHorde greeting\n\nRun `python main.py` to print a greeting.\n\nThis is fixed prototype output; no inference occurred.\n'],
]) {
  const temporary = `/output/.${name}.${randomUUID()}.tmp`;
  const fd = openSync(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL, 0o640);
  try {
    writeFileSync(fd, text);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(temporary, `/output/${name}`);
}
const directory = openSync('/output', constants.O_RDONLY | constants.O_DIRECTORY);
try { fsyncSync(directory); } finally { closeSync(directory); }
console.log(JSON.stringify({ event: 'factory_completed', files: ['main.py', 'README.md'] }));
