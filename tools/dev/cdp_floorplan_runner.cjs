"use strict";

const http = require("http");
const WebSocket = require("../../web/node_modules/next/dist/compiled/ws");

function getJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    }).on("error", reject);
  });
}

class CdpClient {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.ready = new Promise((resolve, reject) => {
      this.socket.once("open", resolve);
      this.socket.once("error", reject);
    });
    this.socket.on("message", (raw) => {
      const message = JSON.parse(String(raw));
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    });
  }

  async command(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression) {
    const result = await this.command("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || "Runtime evaluation failed");
    }
    return result.result?.value;
  }

  close() {
    this.socket.close();
  }
}

async function main() {
  const port = Number(process.env.CDP_PORT || "9233");
  const command = process.argv[2] || "inspect";
  const pages = await getJson(`http://127.0.0.1:${port}/json`);
  const target = pages.find((page) => page.type === "page" && page.url.includes("/floorplan/"));
  if (!target) throw new Error("No /floorplan/ Chrome target found");

  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.command("Runtime.enable");

  if (command === "inspect") {
    const state = await client.evaluate(`({
      title: document.title,
      url: location.href,
      readyState: document.readyState,
      referenceCheckpoint: localStorage.getItem('whole_home_reference_render_checkpoint'),
      webgl: (() => {
        const gl = document.createElement('canvas').getContext('webgl');
        if (!gl) return { available: false };
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        return {
          available: true,
          vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
          renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
        };
      })(),
      text: document.body.innerText.slice(0, 12000),
      buttons: Array.from(document.querySelectorAll('button')).map((button, index) => ({
        index,
        text: button.innerText.trim(),
        disabled: button.disabled,
      })),
    })`);
    process.stdout.write(`${JSON.stringify(state, null, 2)}\n`);
  } else if (command === "click-project") {
    const state = await client.evaluate(`(() => {
      const button = Array.from(document.querySelectorAll('button')).find((element) =>
        element.innerText.includes('CAD+AI 混合模型：5 个空间、11 个门窗、17 个语义锚点')
      );
      if (!button) return { clicked: false };
      button.click();
      return { clicked: true, text: button.innerText };
    })()`);
    process.stdout.write(`${JSON.stringify(state, null, 2)}\n`);
  } else if (command === "click-preflight") {
    const state = await client.evaluate(`(() => {
      const button = Array.from(document.querySelectorAll('button')).find((item) =>
        item.innerText.includes('自动生成 9-slot 浏览器证据')
      );
      if (!button) return { clicked: false, reason: 'button_not_found' };
      if (button.disabled) return { clicked: false, reason: 'button_disabled', text: button.innerText };
      button.click();
      return { clicked: true, text: button.innerText };
    })()`);
    process.stdout.write(`${JSON.stringify(state, null, 2)}\n`);
  } else if (command === "reload") {
    await client.command("Network.enable");
    await client.command("Network.clearBrowserCache");
    await client.command("Page.reload", { ignoreCache: true });
    process.stdout.write("reloaded\n");
  } else {
    throw new Error(`Unknown command: ${command}`);
  }

  client.close();
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
