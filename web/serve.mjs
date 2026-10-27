import { spawn } from 'node:child_process';

// Keep ./dev.sh's port and host contract. Angular fails on occupied ports in
// non-interactive mode, rather than silently moving to another application.
const args = process.argv.slice(2).filter((arg) => arg !== '--strictPort');
const hostIndex = args.indexOf('--host');
if (hostIndex >= 0 && (!args[hostIndex + 1] || args[hostIndex + 1].startsWith('--'))) {
  args.splice(hostIndex + 1, 0, '0.0.0.0');
}
const child = spawn(process.execPath, [
  'node_modules/@angular/cli/bin/ng.js', 'serve',
  ...(args.includes('--host') ? [] : ['--host', '0.0.0.0']),
  ...(args.includes('--port') ? [] : ['--port', process.env.WEB_PORT || '8173']),
  '--proxy-config', 'proxy.conf.cjs', ...args,
], { stdio: 'inherit', env: { ...process.env, CI: 'true' } });
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
child.on('exit', (code) => process.exit(code ?? 0));
