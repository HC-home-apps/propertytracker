/**
 * Cloudflare Worker — Telegram webhook for PropertyTracker review buttons.
 *
 * When a user taps Yes/No on a review button in Telegram, this worker:
 * 1. Acknowledges the tap (removes loading spinner)
 * 2. Edits the message to remove buttons and show the verdict
 * 3. Triggers a GitHub Actions workflow to update the database
 *
 * Required secrets (set via `wrangler secret put`):
 *   TELEGRAM_BOT_TOKEN  - Telegram bot token
 *   WEBHOOK_SECRET       - Secret token for verifying Telegram requests
 *   GITHUB_TOKEN         - GitHub PAT with repo scope (for repository_dispatch)
 *
 * Required vars (set in wrangler.toml [vars]):
 *   GITHUB_REPO          - e.g. "HC-home-apps/propertytracker"
 */

const TELEGRAM_API = "https://api.telegram.org/bot";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    // Verify the request is from Telegram
    const secretToken = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secretToken !== env.WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    const update = await request.json();
    const callback = update.callback_query;

    // Only handle callback queries (button taps)
    if (!callback) {
      return new Response("OK", { status: 200 });
    }

    const callbackId = callback.id;
    const data = callback.data || "";
    const message = callback.message || {};
    const chatId = message.chat?.id;
    const messageId = message.message_id;
    const originalText = message.text || "";

    // Parse callback data: "review:SEGMENT:SALE_ID:yes/no"
    const parts = data.split(":");
    if (parts.length !== 4 || parts[0] !== "review") {
      await answerCallback(env.TELEGRAM_BOT_TOKEN, callbackId, "Unknown action");
      return new Response("OK", { status: 200 });
    }

    const [, segmentCode, saleId, response] = parts;
    const isYes = response === "yes";
    const verdict = isYes ? "YES - Comparable" : "NO - Not comparable";
    const toastText = isYes ? "Marked as comparable" : "Marked as not comparable";

    // 1. Acknowledge the tap (removes loading spinner, shows toast)
    await answerCallback(env.TELEGRAM_BOT_TOKEN, callbackId, toastText);

    // 2. Edit message: remove buttons, replace question with verdict
    if (chatId && messageId) {
      const newText = originalText.replace(
        "Is this comparable to your property?",
        verdict
      );
      await editMessage(env.TELEGRAM_BOT_TOKEN, chatId, messageId, newText);
    }

    // 3. Trigger GitHub Actions to update the database
    if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
      await triggerDbUpdate(
        env.GITHUB_TOKEN,
        env.GITHUB_REPO,
        saleId,
        response,
        segmentCode
      );
    }

    return new Response("OK", { status: 200 });
  },
};

async function answerCallback(botToken, callbackId, text) {
  await fetch(`${TELEGRAM_API}${botToken}/answerCallbackQuery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ callback_query_id: callbackId, text }),
  });
}

async function editMessage(botToken, chatId, messageId, newText) {
  await fetch(`${TELEGRAM_API}${botToken}/editMessageText`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      message_id: messageId,
      text: newText,
    }),
  });
}

async function triggerDbUpdate(githubToken, repo, saleId, response, segmentCode) {
  await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `token ${githubToken}`,
      Accept: "application/vnd.github.v3+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      event_type: "review-response",
      client_payload: {
        sale_id: saleId,
        response: response,
        segment_code: segmentCode,
      },
    }),
  });
}
