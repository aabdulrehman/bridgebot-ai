/** Quick-start snippets shown after linking a bot (also used by the docs). */
export function botSnippets(base: string, token: string, framework: string, secret?: string): { title: string; code: string }[] {
  const sdk = `curl -O ${base}/downloads/bridgebot_sdk.py`;
  const common = [
    { title: "1 · Test the token", code: `curl ${base}/api/bot/me -H "Authorization: Bot ${token}"` },
    { title: "2 · Long-poll messages (Telegram-style)", code: `curl "${base}/api/bot/updates?offset=0&timeout=25" -H "Authorization: Bot ${token}"` },
    { title: "3 · Reply", code: `curl -X POST ${base}/api/bot/messages -H "Authorization: Bot ${token}" -H "Content-Type: application/json" \\\n  -d '{"reply_to": 1, "text": "Hello from my bot"}'` },
  ];
  const adapters: Record<string, { title: string; code: string }> = {
    hermes: { title: "Hermes Agent adapter (no code)", code: `${sdk}\npython bridgebot_sdk.py --server ${base} --token ${token} \\\n  --exec "hermes chat --oneshot -Q -q {text}" \\\n  --skills-dir ~/.hermes/skills --skill-format skillmd` },
    openclaw: { title: "OpenClaw adapter (no code)", code: `${sdk}\npython bridgebot_sdk.py --server ${base} --token ${token} \\\n  --exec "openclaw agent --message {text}" \\\n  --skills-dir ~/.openclaw/workspace/skills --skill-format skillmd` },
    n8n: { title: "n8n: Webhook trigger → AI node → Respond to Webhook", code: `# Set delivery = webhook and paste your n8n Production webhook URL.\n# BridgeBot POSTs {event, bot_id, update:{update_id, kind, text, payload}} signed with X-BridgeBot-Signature.\n# Reply synchronously from "Respond to Webhook" with JSON:  {"text": "{{$json.output}}"}\n# Or without a public URL:\n${sdk}\npython bridgebot_sdk.py --server ${base} --token ${token} --http http://localhost:5678/webhook/my-bot` },
    langchain: { title: "LangChain / any Python bot", code: `${sdk}\n\nfrom bridgebot_sdk import BridgeBot\nbot = BridgeBot("${base}", "${token}", skills_dir="./skills")\n\n@bot.on_message\ndef handle(update):\n    return my_chain.invoke(update["text"])   # your agent here\n\nbot.run()` },
    http: { title: "Generic HTTP bot", code: `${sdk}\npython bridgebot_sdk.py --server ${base} --token ${token} --http http://localhost:8080/chat\n# your bot receives {"text": ..., "update": {...}} and returns {"text": "reply"}` },
    grok: { title: "External cloud bot (e.g. Grok Bot)", code: `# Give your bot these two HTTP calls — that's the whole integration:\nGET  ${base}/api/bot/updates?offset=<last_update_id>&timeout=25   (Authorization: Bot ${token})\nPOST ${base}/api/bot/messages {"reply_to": <update_id>, "text": "..."}\nPOST ${base}/api/bot/ack {"update_id": <id>, "ok": true}   # after installing a skill_inject` },
    cli: { title: "Any CLI bot", code: `${sdk}\npython bridgebot_sdk.py --server ${base} --token ${token} --exec "ollama run llama3.2 {text}"` },
  };
  const ws = { title: "WebSocket (realtime)", code: `wscat -c "${base.replace(/^http/, "ws")}/ws/bot?token=${token}"\n> {"type":"MESSAGE","reply_to":1,"text":"hi"}\n> {"type":"ACK","update_id":2,"ok":true}` };
  const out = [adapters[framework] ?? adapters.http, ...common, ws];
  if (secret) out.push({ title: "Webhook signing secret (verify X-BridgeBot-Signature)", code: secret });
  return out;
}
