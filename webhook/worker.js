/**
 * Cloudflare Worker — Telegram webhook for PropertyTracker review buttons.
 *
 * When a user taps Yes/No on a review button in Telegram, this worker:
 * 1. Acknowledges the tap (removes loading spinner)
 * 2. Edits the message to add verdict emojis and remove decided buttons
 * 3. Triggers a GitHub Actions workflow to update the database
 *
 * Supports both individual and bulk callbacks:
 * - Individual: "review:SEGMENT:SALE_ID:yes/no" — marks one line, removes one button row
 * - Bulk: "review:SEGMENT:all:yes/no" — marks all lines, removes all buttons
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
    const emoji = isYes ? "✅" : "❌";
    const toastText = isYes ? "Marked as comparable" : "Marked as not comparable";

    // 1. Acknowledge the tap (removes loading spinner, shows toast)
    await answerCallback(env.TELEGRAM_BOT_TOKEN, callbackId, toastText);

    // 2. Handle bulk vs individual
    const isBulk = saleId === "all";
    const keyboard = message.reply_markup?.inline_keyboard || [];
    let saleIdsToUpdate = [];
    let newText = originalText;
    let newKeyboard = null;

    if (isBulk) {
      // Extract all sale IDs from keyboard
      saleIdsToUpdate = extractSaleIds(keyboard, segmentCode);

      // Add verdict emoji to all lines
      newText = addVerdictToAllLines(originalText, emoji);

      // Remove all buttons
      newKeyboard = { inline_keyboard: [] };
    } else {
      // Individual sale
      saleIdsToUpdate = [saleId];

      // Find which line number this sale is (by button position)
      let lineNumber = null;
      for (let i = 0; i < keyboard.length; i++) {
        const row = keyboard[i];
        for (const button of row) {
          const btnParts = button.callback_data.split(':');
          if (btnParts[2] === saleId) {
            lineNumber = i + 1; // 1-indexed
            break;
          }
        }
        if (lineNumber) break;
      }

      if (lineNumber) {
        newText = addVerdictToMessage(originalText, lineNumber, emoji);
      }

      // Remove only this sale's button row
      newKeyboard = {
        inline_keyboard: removeButtonRow(keyboard, saleId)
      };
    }

    // 3. Edit message with verdict emojis and updated keyboard
    if (chatId && messageId) {
      await editMessage(env.TELEGRAM_BOT_TOKEN, chatId, messageId, newText, newKeyboard);
    }

    // 4. Trigger GitHub Actions to update the database
    if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
      await triggerDbUpdate(
        env.GITHUB_TOKEN,
        env.GITHUB_REPO,
        saleIdsToUpdate,
        response,
        segmentCode
      );
    }

    return new Response("OK", { status: 200 });
  },
};

/**
 * Extract all sale IDs from inline keyboard buttons.
 * Filters out the 'all' bulk buttons.
 */
function extractSaleIds(keyboard, segmentCode) {
  const saleIds = [];
  for (const row of keyboard) {
    for (const button of row) {
      const parts = button.callback_data.split(':');
      if (parts.length === 4 && parts[0] === 'review' && parts[1] === segmentCode && parts[2] !== 'all') {
        if (!saleIds.includes(parts[2])) {
          saleIds.push(parts[2]);
        }
      }
    }
  }
  return saleIds;
}

/**
 * Add verdict emoji to a specific line number in the message.
 * Example: "1. <a href=...>15 Alliance Ave</a>" → "1. ✅ <a href=...>15 Alliance Ave</a>"
 */
function addVerdictToMessage(text, lineNumber, emoji) {
  const lines = text.split('\n');
  const updatedLines = lines.map(line => {
    // Check if this line starts with the number we're looking for
    const regex = new RegExp(`^${lineNumber}\\.\\s`);
    if (regex.test(line)) {
      return line.replace(regex, `${lineNumber}. ${emoji} `);
    }
    return line;
  });
  return updatedLines.join('\n');
}

/**
 * Add verdict emoji to all numbered lines in the message.
 * Example: "1. <a href=...>..." → "1. ✅ <a href=...>..."
 */
function addVerdictToAllLines(text, emoji) {
  const lines = text.split('\n');
  const updatedLines = lines.map(line => {
    // Match lines starting with a number followed by a period
    if (/^\d+\.\s/.test(line)) {
      return line.replace(/^(\d+)\.\s/, `$1. ${emoji} `);
    }
    return line;
  });
  return updatedLines.join('\n');
}

/**
 * Remove the button row for a specific sale ID.
 * Keeps all other rows including the bulk "All" row.
 */
function removeButtonRow(keyboard, saleId) {
  return keyboard.filter(row => {
    // Keep rows that don't match this saleId
    return !row.some(button => {
      const parts = button.callback_data.split(':');
      return parts[2] === saleId;
    });
  });
}

async function answerCallback(botToken, callbackId, text) {
  await fetch(`${TELEGRAM_API}${botToken}/answerCallbackQuery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ callback_query_id: callbackId, text }),
  });
}

async function editMessage(botToken, chatId, messageId, newText, newKeyboard = null) {
  const payload = {
    chat_id: chatId,
    message_id: messageId,
    text: newText,
    parse_mode: 'HTML',
  };

  if (newKeyboard !== null) {
    if (newKeyboard.inline_keyboard && newKeyboard.inline_keyboard.length > 0) {
      payload.reply_markup = newKeyboard;
    } else {
      // Empty keyboard means remove all buttons
      payload.reply_markup = { inline_keyboard: [] };
    }
  }

  await fetch(`${TELEGRAM_API}${botToken}/editMessageText`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function triggerDbUpdate(githubToken, repo, saleIds, response, segmentCode) {
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
        sale_ids: Array.isArray(saleIds) ? saleIds : [saleIds],
        response: response,
        segment_code: segmentCode,
      },
    }),
  });
}
