// End-to-end check of a running relay: `node test/live.mjs <url>`.
// Plays a PC and a phone, the way core/mobile_relay.py and the app do, and
// checks that a message goes through both ways, that a stranger cannot take
// the PC's room, and that a phone cannot join a room with no PC in it. The
// relay workflow runs it against the real Worker right after each deploy.
import { roomForToken } from "../src/protocol.js";

const base = (process.argv[2] || "http://127.0.0.1:8787").replace(/\/+$/, "");
const ws = (path) => base.replace(/^http/, "ws") + path;
const b64url = (bytes) => Buffer.from(bytes).toString("base64url");

function open(url, headers) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url, headers ? { headers } : undefined);
    const timer = setTimeout(() => reject(new Error(`timeout opening ${url}`)), 15000);
    socket.addEventListener("open", () => { clearTimeout(timer); resolve(socket); });
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error(`refused: ${url}`)); });
  });
}

function next(socket) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout waiting for a message")), 15000);
    socket.addEventListener("message", (event) => { clearTimeout(timer); resolve(JSON.parse(event.data)); }, { once: true });
  });
}

async function refused(url, headers) {
  try { (await open(url, headers)).close(); return false; } catch { return true; }
}

const token = b64url(crypto.getRandomValues(new Uint8Array(32)));
const room = await roomForToken(token);

if (!(await refused(ws(`/v1/phone/${room}`)))) throw new Error("a phone joined a room with no PC");
const stranger = b64url(crypto.getRandomValues(new Uint8Array(32)));
if (!(await refused(ws(`/v1/pc/${room}`), { Authorization: `Bearer ${stranger}` }))) {
  throw new Error("a stranger took the PC's room");
}

const pc = await open(ws(`/v1/pc/${room}`), { Authorization: `Bearer ${token}` });
const phone = await open(ws(`/v1/phone/${room}`));

const sealed = { d: "dev_test", n: "bm9uY2U", c: "c2VhbGVk" };
const atPc = next(pc);
phone.send(JSON.stringify({ t: "rpc", q: 7, b: sealed }));
const frame = await atPc;
if (frame.t !== "rpc" || frame.q !== 7 || !/^[0-9a-f]{32}$/.test(frame.c) || frame.b.c !== sealed.c) {
  throw new Error(`the PC got ${JSON.stringify(frame)}`);
}
const atPhone = next(phone);
pc.send(JSON.stringify({ q: 7, c: frame.c, s: 200, b: { n: "cmVwbHk", c: "YW5zd2Vy" } }));
const reply = await atPhone;
if (reply.q !== 7 || reply.s !== 200 || reply.b.c !== "YW5zd2Vy") throw new Error(`the phone got ${JSON.stringify(reply)}`);

// The PC leaves: the next request is answered "pc_offline" by the relay itself.
pc.close();
await new Promise((resolve) => setTimeout(resolve, 1500));
const offline = next(phone);
phone.send(JSON.stringify({ t: "rpc", q: 8, b: sealed }));
const answer = await offline;
if (answer.q !== 8 || answer.s !== 503) throw new Error(`after the PC left the phone got ${JSON.stringify(answer)}`);
phone.close();
console.log(`relay OK at ${base}`);
process.exit(0);
