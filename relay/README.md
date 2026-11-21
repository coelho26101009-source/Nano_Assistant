# NANO relay

Lets the NANO phone app reach the NANO on the PC **away from home**, with
nothing to set up and no port opened anywhere.

```
phone ──wss──▶  relay (Cloudflare Worker)  ◀──wss── PC
          sealed envelopes only; the relay cannot open them
```

- The PC keeps **one outgoing** WebSocket to its room (`core/mobile_relay.py`).
  Nothing ever connects in to the PC.
- The phone connects to the same room (`mobile/src/lib/relay.ts`) and sends
  exactly what it sends on the home Wi-Fi: the AES-256-GCM envelope sealed with
  its own key (`core/mobile_link.py`). The relay sees a room name, sizes and
  times. It cannot read, change or replay a message.
- A room's name is SHA-256 of a random 256-bit token only the PC holds; the PC
  proves the token when it connects. The relay has no accounts and stores
  nothing. Phones learn the room from the pairing QR code (or, for phones paired
  earlier, from the PC over the sealed link).
- Pairing over the relay still needs the person's yes **on the PC** and the same
  six digits on both screens. Removing a phone on the PC revokes it at once;
  removing the last one also throws the room away.

## Cost

Built for the Workers **free** plan: a Durable Object per room with the
WebSocket Hibernation API, so a room with nothing to forward sleeps and costs
nothing. The free plan allows 100,000 requests a day; incoming WebSocket
messages count 20 to 1, so a phone in use all day is a few hundred.

## Deploy (once)

1. Free Cloudflare account; open **Workers & Pages** once so the account gets
   its `*.workers.dev` subdomain, and copy the **Account ID**.
2. API token from the **Edit Cloudflare Workers** template.
3. GitHub → Settings → Secrets and variables → Actions:
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.

`.github/workflows/relay.yml` then tests and deploys every change on `main`
(Actions → Relay → Run workflow deploys by hand). The address it prints goes
into `DEFAULT_RELAY_URL` in `core/mobile_relay.py`. A self-hosted relay can be
used instead with the `NANO_RELAY_URL` environment variable on the PC.

## Develop

```
npm ci
npm test                 # wire rules (shared vector with tests/test_mobile_relay.py)
npx wrangler dev         # the real Worker on http://127.0.0.1:8787
NANO_RELAY_E2E_URL=http://127.0.0.1:8787 python -m pytest tests/test_mobile_relay.py
```
