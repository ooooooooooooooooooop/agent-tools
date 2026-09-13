import { spawn } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const WebSocket = require('ws');

const CHROME_PATH = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const TARGET_URL = 'http://127.0.0.1:3080/';
const CDP_PORT = 9222;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(e);
        }
      });
    }).on('error', reject);
  });
}

async function runBrowserE2E() {
  console.log('=== STARTING ADVERSARIAL BROWSER E2E VIA CDP ===');
  
  // 1. Launch Chrome Headless
  const chromeArgs = [
    '--headless=new',
    `--remote-debugging-port=${CDP_PORT}`,
    '--disable-gpu',
    '--no-sandbox',
    '--disable-extensions',
    '--window-size=1440,900',
    'about:blank'
  ];

  const chromeProc = spawn(CHROME_PATH, chromeArgs, { stdio: 'ignore' });

  try {
    // Wait for CDP to be ready
    let versionInfo = null;
    for (let i = 0; i < 20; i++) {
      try {
        versionInfo = await fetchJson(`http://127.0.0.1:${CDP_PORT}/json/version`);
        if (versionInfo) break;
      } catch (e) {
        await sleep(250);
      }
    }

    if (!versionInfo) {
      throw new Error('Chrome CDP port did not become ready');
    }

    console.log('Headless Chrome started:', versionInfo.Browser);

    // Get list of targets / pages
    const targets = await fetchJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
    const pageTarget = targets.find((t) => t.type === 'page') || targets[0];
    if (!pageTarget || !pageTarget.webSocketDebuggerUrl) {
      throw new Error('No page target available in Chrome');
    }

    console.log('Connecting WebSocket to target:', pageTarget.id);
    const ws = new WebSocket(pageTarget.webSocketDebuggerUrl);

    await new Promise((resolve, reject) => {
      ws.on('open', resolve);
      ws.on('error', reject);
    });

    let msgId = 1;
    const pendingCallbacks = new Map();
    const consoleMessages = [];
    const pageExceptions = [];

    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.id && pendingCallbacks.has(msg.id)) {
        const { resolve, reject } = pendingCallbacks.get(msg.id);
        pendingCallbacks.delete(msg.id);
        if (msg.error) {
          reject(new Error(JSON.stringify(msg.error)));
        } else {
          resolve(msg.result);
        }
      } else if (msg.method === 'Runtime.consoleAPICalled') {
        const args = (msg.params.args || []).map((a) => a.value || a.description).join(' ');
        consoleMessages.push({ type: msg.params.type, text: args });
      } else if (msg.method === 'Runtime.exceptionThrown') {
        pageExceptions.push(msg.params.exceptionDetails);
      }
    });

    function sendCommand(method, params = {}) {
      const id = msgId++;
      return new Promise((resolve, reject) => {
        pendingCallbacks.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    }

    // Enable CDP domains
    await sendCommand('Page.enable');
    await sendCommand('Runtime.enable');
    await sendCommand('DOM.enable');

    console.log('Navigating to:', TARGET_URL);
    await sendCommand('Page.navigate', { url: TARGET_URL });

    // Wait for page settlement
    console.log('Waiting for frontend boot and DOM settlement (5000ms)...');
    await sleep(5000);

    // Evaluate in browser context
    const evalRes = await sendCommand('Runtime.evaluate', {
      expression: `(() => {
        const root = document.getElementById('root');
        const rootChildCount = root ? root.children.length : 0;
        const rootHtmlSnippet = root ? root.innerHTML.slice(0, 1000) : '';
        const bodyText = document.body.innerText || '';
        const hasPluginError = bodyText.includes('Failed to load plugins') ||
                               bodyText.includes('pending (waiting for services');
        const hasBootError = bodyText.includes('client-modules:') ||
                             bodyText.includes('Error:');
        
        // Find conversation UI or chat elements
        const hasConversationView = !!(
          document.querySelector('[data-view="conversation"]') ||
          document.querySelector('.conversation') ||
          document.querySelector('[class*="conversation"]') ||
          document.querySelector('textarea') ||
          document.querySelector('[contenteditable="true"]')
        );

        // Find sidebar or workspaces
        const hasSidebar = !!(
          document.querySelector('[class*="sidebar"]') ||
          document.querySelector('aside') ||
          document.querySelector('nav')
        );

        return {
          title: document.title,
          rootChildCount,
          hasPluginError,
          hasBootError,
          hasConversationView,
          hasSidebar,
          bodySnippet: bodyText.slice(0, 500),
          htmlLength: document.documentElement.outerHTML.length
        };
      })()`,
      returnByValue: true
    });

    const pageState = evalRes.result.value;
    console.log('=== BROWSER EVALUATION RESULTS ===');
    console.log(JSON.stringify(pageState, null, 2));

    // Capture screenshot as durable machine evidence
    console.log('Capturing screenshot...');
    const screenshotRes = await sendCommand('Page.captureScreenshot', { format: 'png' });
    const screenshotPath = path.resolve('browser-e2e-screenshot.png');
    fs.writeFileSync(screenshotPath, Buffer.from(screenshotRes.data, 'base64'));
    console.log('Screenshot saved to:', screenshotPath, `(${fs.statSync(screenshotPath).size} bytes)`);

    ws.close();

    const report = {
      browser: versionInfo.Browser,
      target_url: TARGET_URL,
      page_title: pageState.title,
      react_root_rendered: pageState.rootChildCount > 0,
      conversation_view_rendered: pageState.hasConversationView,
      sidebar_rendered: pageState.hasSidebar,
      failed_to_load_plugins_present: pageState.hasPluginError,
      boot_error_present: pageState.hasBootError,
      exceptions_count: pageExceptions.length,
      console_messages_count: consoleMessages.length,
      screenshot_file: screenshotPath,
      overall_browser_e2e: (!pageState.hasPluginError && !pageState.hasBootError && pageState.rootChildCount > 0)
        ? 'PASS' : 'FAIL'
    };

    console.log('\n=== FINAL BROWSER E2E REPORT ===');
    console.log(JSON.stringify(report, null, 2));

    if (report.overall_browser_e2e !== 'PASS') {
      throw new Error('Browser E2E failed verification');
    }
    return report;
  } finally {
    chromeProc.kill();
  }
}

runBrowserE2E().catch((err) => {
  console.error('E2E ERROR:', err);
  process.exit(1);
});
