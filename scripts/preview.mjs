// A no-dependency preview entry point for the real Python UI and API.
import {spawn} from 'node:child_process';

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf(name);
  return at === -1 || !args[at + 1] ? fallback : args[at + 1];
};
const host = option('--host', '0.0.0.0');
const port = option('--port', '4173');
const child = spawn('uv', [
  'run', '--extra', 'dev', 'uvicorn', 'clipforge.app:app',
  '--host', host, '--port', port
], {stdio: 'inherit'});
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => child.kill(signal));
}
child.on('exit', (code) => { process.exitCode = code ?? 1; });
