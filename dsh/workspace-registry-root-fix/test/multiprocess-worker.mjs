// multiprocess-worker.mjs — Child process worker for true multi-process storage tests.
// Runs as an independent Node OS process, communicating with test runner via IPC.
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const HOME = process.env.USERPROFILE;
const BASE = join(HOME, '.dsh', 'profiles', 'web', 'base-dsh-0.1.1-rc.2',
  'node_modules', '@deepseek-ai', 'dsh', 'node_modules', '@deepseek-ai');
const storageJsonUrl = pathToFileURL(join(BASE, 'dsh-storage-json', 'lib', 'index.js')).href;

const { JsonStorageBackend } = await import(storageJsonUrl);

let backend = null;
let unit = null;

process.on('message', async (msg) => {
  const { id, action } = msg;
  try {
    switch (action) {
      case 'ping': {
        process.send({ id, status: 'ok', pid: process.pid });
        break;
      }
      case 'open': {
        const { dir, descriptor } = msg;
        backend = new JsonStorageBackend(dir);
        unit = await backend.kv.open(descriptor);
        process.send({ id, status: 'ok', pid: process.pid });
        break;
      }
      case 'loadAll': {
        if (!unit) throw new Error('Unit not open');
        const state = await unit.loadAll();
        process.send({ id, status: 'ok', pid: process.pid, state });
        break;
      }
      case 'putRecord': {
        if (!unit) throw new Error('Unit not open');
        const { table, key, value } = msg;
        await unit.putRecord(table, key, value);
        process.send({ id, status: 'ok', pid: process.pid });
        break;
      }
      case 'setGlobal': {
        if (!unit) throw new Error('Unit not open');
        const { value } = msg;
        await unit.setGlobal(value);
        process.send({ id, status: 'ok', pid: process.pid });
        break;
      }
      case 'close': {
        if (unit) {
          await unit.close();
          unit = null;
        }
        if (backend) {
          await backend.close();
          backend = null;
        }
        process.send({ id, status: 'ok', pid: process.pid });
        break;
      }
      case 'exit': {
        if (unit) await unit.close().catch(() => {});
        if (backend) await backend.close().catch(() => {});
        process.send({ id, status: 'ok', pid: process.pid });
        process.exit(0);
        break;
      }
      default:
        throw new Error(`Unknown action: ${action}`);
    }
  } catch (err) {
    process.send({
      id,
      status: 'error',
      pid: process.pid,
      error: err.message,
      stack: err.stack,
    });
  }
});
