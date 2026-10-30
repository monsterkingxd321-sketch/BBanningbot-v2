from telethon import events, Button

from bot import bot, db, active_reports, active_operations
from config import MAX_REPORTS_PER_ACCOUNT
from utils.helpers import is_user_authorized, scrape_recent_messages
from utils.report_options import load_report_choices
from utils.review_utils import redact_sensitive
from handlers.report_ui import (
    count_buttons,
    scrape_buttons,
    delay_buttons,
    dist_mode_buttons,
    exec_mode_buttons,
    comment_buttons,
    peer_reason_buttons,
    report_option_buttons,
    nav_buttons,
    with_nav,
)


def _channel_label(channel: dict) -> str:
    label = channel.get("title") or channel.get("username") or channel.get("identifier") or "Channel"
    label = str(label).replace("\n", " ").strip()
    return label[:20]


async def _send_or_edit(event, text: str, buttons=None, send: bool = True):
    if send:
        return await event.respond(text, buttons=buttons)
    try:
        return await event.edit(text, buttons=buttons)
    except Exception:
        return await event.respond(text, buttons=buttons)


async def show_report_start(event, edit: bool = False):
    """Show the first reporting step with saved-channel shortcuts."""
    user_id = event.sender_id

    if not is_user_authorized(user_id):
        if edit:
            await event.edit("Access denied.")
        else:
            await event.respond("Access denied.")
        return

    if user_id in active_reports:
        text = "A report session is already running. Stop it before starting another."
        buttons = [
            [Button.inline("Stop Report", b"stop_report")],
            [Button.inline("Main Menu", b"menu:home")],
        ]
        await _send_or_edit(event, text, buttons, send=not edit)
        return

    if user_id in active_operations:
        text = (
            "A login or OTP process is still running.\n\n"
            "Complete OTP/2FA first, or cancel the login."
        )
        buttons = [
            [Button.inline("Cancel Login", b"ui_cancel")],
            [Button.inline("Main Menu", b"menu:home")],
        ]
        await _send_or_edit(event, text, buttons, send=not edit)
        return

    db.set_user_state(user_id, "report_target", {"step": 1})
    channels = []
    try:
        channels = db.get_recent_review_channels(5)
    except Exception:
        channels = []

    buttons = []
    for channel in channels:
        buttons.append([
            Button.inline(_channel_label(channel), f"report_saved:{channel['id']}".encode())
        ])
    buttons.append([Button.inline("Type / Paste Channel Link", b"report_manual_target")])
    buttons.append([Button.inline("Main Menu", b"menu:home")])

    text = (
        "**Report Process - Step 1/6**\n\n"
        "Choose the target group or channel:\n"
        "- Tap a saved channel button, or\n"
        "- Send an `@username` or `https://t.me/...` link.\n\n"
        "When you confirm, the bot first joins the target with every active account. "
        "Only accounts that joined will send reports."
    )
    await _send_or_edit(event, text, buttons, send=not edit)


async def show_manual_target_prompt(event, send: bool = True):
    user_id = event.sender_id
    db.set_user_state(user_id, "report_target", {"step": 1})
    text = (
        "**Step 1/6 - Target**\n\n"
        "Send the group or channel link or username:\n"
        "Example: `@channel_name`\n"
        "Example: `https://t.me/channel_name`\n\n"
        "A private invite link (`t.me/+...` or `t.me/joinchat/...`) can be used "
        "for reporting specific messages."
    )
    await _send_or_edit(event, text, nav_buttons(), send=send)


async def show_target_type(event, data: dict, send: bool = True):
    """Ask whether to report specific messages, the whole peer, or profile media."""
    user_id = event.sender_id
    db.set_user_state(user_id, "report_target", data)
    target = str(data.get('target', ''))
    target_lower = target.lower()
    is_invite = (
        't.me/+' in target_lower
        or 'joinchat/' in target_lower
        or target.strip().startswith('+')
        or 'tg://join' in target_lower
    )
    rows = [
        [Button.inline("💬 Specific Messages", b"target_messages")],
    ]
    if not is_invite:
        rows[0].append(Button.inline("🏢 Whole Peer", b"target_peer"))
    rows.append([Button.inline("🖼️ Profile Media (PFP / Video)", b"target_pfp")])
    rows.append([Button.inline("Main Menu", b"menu:home")])
    text = (
        "**Step 2/6 - Target Type**\n\n"
        f"Target: `{target}`\n\n"
        "Select what you want to report:\n"
        "- **Specific Messages**: recent posts, manual links, or custom range\n"
        "- **Whole Peer**: whole channel/group or user account\n"
        "- **Profile Media**: profile pictures / videos (single, all, custom)"
    )
    if is_invite:
        text += (
            "\n\nPrivate invite targets do not support whole-channel peer reports. "
            "Select specific messages or profile media."
        )
    await _send_or_edit(event, text, rows, send=send)


async def show_pfp_mode_step(event, data: dict, send: bool = True):
    """Show options for reporting profile photo/video."""
    user_id = event.sender_id
    db.set_user_state(user_id, "report_pfp_mode", data)
    text = (
        "**Step 2/6 - Profile Media (PFP / Video)**\n\n"
        f"Target: `{data.get('target', '')}`\n\n"
        "Choose profile media reporting option:\n"
        "- **Single Pic (Latest)**: reports the current profile picture/video\n"
        "- **All Images / Videos**: reports all profile pictures/videos in history\n"
        "- **Custom (By Number)**: choose specific photo/video number(s) (e.g. 1, 2)"
    )
    buttons = [
        [Button.inline("Single Pic (Latest)", b"pfp_mode:single"),
         Button.inline("All Images / Videos", b"pfp_mode:all")],
        [Button.inline("Custom (By Number)", b"pfp_mode:custom")],
        [Button.inline("Back", b"report_target_back"),
         Button.inline("Main Menu", b"menu:home")],
    ]
    await _send_or_edit(event, text, buttons, send=send)


async def show_pfp_custom_prompt(event, data: dict, send: bool = True):
    """Prompt user to send specific photo/video numbers."""
    user_id = event.sender_id
    db.set_user_state(user_id, "report_pfp_custom", data)
    text = (
        "**Custom Profile Media Numbers**\n\n"
        f"Target: `{data.get('target', '')}`\n\n"
        "Send the photo/video number(s) to report:\n"
        "- Example: `1` (for current/first profile photo)\n"
        "- Example: `1, 2, 3` (for 1st, 2nd, and 3rd photos)\n"
        "- Example: `1-5` (for first 5 photos)"
    )
    buttons = [
        [Button.inline("Back", b"target_pfp")],
        [Button.inline("Main Menu", b"menu:home")],
    ]
    await _send_or_edit(event, text, buttons, send=send)


async def show_pfp_count_step(event, data: dict, send: bool = True):
    """Show count step for profile photo reporting."""
    mode_desc = data.get("pfp_mode", "single")
    if mode_desc == "custom":
        mode_desc = f"custom #{data.get('pfp_indices', [1])}"
    await show_count_step(
        event,
        data,
        send=send,
        state="report_pfp_count",
        max_count=MAX_REPORTS_PER_ACCOUNT,
        title="Reports Per Account (Profile Media)",
        prefix=f"Profile media mode: `{mode_desc}`.\n\n",
    )


async def show_scrape_prompt(event, data: dict, send: bool = True):
    user_id = event.sender_id
    db.set_user_state(user_id, "report_scrape_count", data)
    text = (
        "**Auto-Scrape Messages**\n\n"
        f"Target: `{data.get('target', '')}`\n\n"
        "How many recent posts should be fetched? Choose a preset or send a custom "
        "number from 1 to 100."
    )
    await _send_or_edit(event, text, with_nav(scrape_buttons()), send=send)


async def show_manual_entry(event, data: dict, send: bool = True, prefix: str = ""):
    user_id = event.sender_id
    db.set_user_state(user_id, "report_messages", data)
    text = (
        f"{prefix}"
        "**Step 2/6 - Manual Message Links**\n\n"
        "Send one message link per line:\n\n"
        "`https://t.me/channel/123`\n"
        "`https://t.me/channel/456`"
    )
    await _send_or_edit(event, text, nav_buttons(), send=send)


async def run_scrape_flow(event, data: dict, limit: int, edit: bool = False):
    """Scrape recent posts using the target, then show the report-count step."""
    if limit < 1 or limit > 100:
        await _send_or_edit(
            event,
            "The scrape count must be between 1 and 100.",
            nav_buttons(),
            send=not edit,
        )
        return

    progress = await _send_or_edit(
        event,
        f"Fetching up to {limit} messages from `{data.get('target', '')}`...",
        send=not edit,
    )

    try:
        scraped = await scrape_recent_messages(data.get("target", ""), limit=limit)
    except Exception as exc:
        error = redact_sensitive(exc, 250)
        data["messages"] = []
        if not edit and progress is not None:
            try:
                await progress.edit(f"Auto-scrape failed: {error}")
            except Exception:
                pass
        await show_manual_entry(
            event,
            data,
            send=not edit,
            prefix=f"Auto-scrape failed: {error}\n\n",
        )
        return

    if not scraped:
        data["messages"] = []
        if not edit and progress is not None:
            try:
                await progress.edit("No messages were found.")
            except Exception:
                pass
        await show_manual_entry(
            event,
            data,
            send=not edit,
            prefix="No messages were found. Send manual message links instead.\n\n",
        )
        return

    data["messages"] = scraped
    if not edit and progress is not None:
        try:
            await progress.edit(f"Fetched {len(scraped)} message(s).")
        except Exception:
            pass
    await show_count_step(
        event,
        data,
        send=not edit,
        state="report_count",
        max_count=MAX_REPORTS_PER_ACCOUNT,
        title="Step 3/6 - Reports Per Account",
        prefix=f"Fetched {len(scraped)} message(s).\n\n",
    )


async def show_count_step(
    event,
    data: dict,
    send: bool = True,
    state: str = "report_count",
    max_count: int = MAX_REPORTS_PER_ACCOUNT,
    title: str = "Step 3/6 - Reports Per Account",
    prefix: str = "",
):
    user_id = event.sender_id
    db.set_user_state(user_id, state, data)
    text = (
        f"{prefix}"
        f"**{title}**\n\n"
        f"Target: `{data.get('target', '')}`\n\n"
        f"How many reports should each account send?\n"
        f"Maximum: {max_count}. Choose a preset or send a custom number."
    )
    await _send_or_edit(event, text, with_nav(count_buttons(max_count)), send=send)


async def show_peer_count_step(event, data: dict, send: bool = True):
    await show_count_step(
        event,
        data,
        send=send,
        state="report_peer_count",
        max_count=MAX_REPORTS_PER_ACCOUNT,
        title="Reports Per Account (Peer)",
        prefix="Whole channel/group peer report selected.\n\n",
    )


async def show_user_count_step(event, data: dict, send: bool = True):
    await show_count_step(
        event,
        data,
        send=send,
        state="report_user_count",
        max_count=MAX_REPORTS_PER_ACCOUNT,
        title="Reports Per Account (User/Bot)",
        prefix=f"Target: `{data.get('target', '')}`\n\n",
    )


async def show_delay_step(event, data: dict, send: bool = True):
    user_id = event.sender_id
    db.set_user_state(user_id, "report_delay", data)
    text = (
        "**Step 4/6 - Delay**\n\n"
        "Choose the delay between report requests. Tap a preset or send a custom "
        "number of seconds.\n\n"
        f"Target: `{data.get('target', '')}`"
    )
    await _send_or_edit(event, text, with_nav(delay_buttons()), send=send)


async def show_dist_mode_step(event, data: dict, send: bool = True):
    user_id = event.sender_id
    db.set_user_state(user_id, "report_dist_mode", data)
    num_msgs = len(data.get('messages', []))
    total_accs = len(db.get_active_accounts()) or 1
    rpm = int(data.get('reports_per_account') or 1)
    
    rr_tot = (num_msgs if total_accs <= num_msgs else total_accs) * rpm
    all_tot = num_msgs * total_accs * rpm

    text = (
        "**Step 5/7 - Message Distribution**\n\n"
        f"Target: `{data.get('target', '')}`\n"
        f"Accounts: {total_accs}\n"
        f"Messages: {num_msgs}\n"
        f"Reports per account: {rpm}\n\n"
        "Choose how accounts should report these messages:\n\n"
        f"1. **1 Account per Message (Spread / Round-Robin)**\n"
        f"Account 1 reports Message 1, Account 2 reports Message 2, etc.\n"
        f"• **Total reports: {rr_tot}** (1 report per message)\n\n"
        f"2. **All Accounts to All Messages (Mass)**\n"
        f"Every active account reports every message link.\n"
        f"• **Total reports: {all_tot}** ({total_accs} accs × {num_msgs} msgs)"
    )
    await _send_or_edit(event, text, with_nav(dist_mode_buttons(), back_data=b"back_to_delay"), send=send)


async def show_exec_mode_step(event, data: dict, send: bool = True, peer: bool = False):
    user_id = event.sender_id
    state = "report_peer_exec_mode"if peer else "report_exec_mode"
    db.set_user_state(user_id, state, data)
    text = (
        "**Execution Mode**\n\n"
        "How should the reports run?\n\n"
        "- **Turbo:** accounts run in parallel\n"
        "- **Safe:** accounts run one after another (recommended)"
    )
    await _send_or_edit(event, text, with_nav(exec_mode_buttons(peer=peer)), send=send)


async def show_comment_choice(event, data: dict, send: bool = True, peer: bool = False):
    user_id = event.sender_id
    state = "report_peer_comment_choice"if peer else "report_comment_choice"
    db.set_user_state(user_id, state, data)
    text = "**Step 5/6 - Comment**\n\nDo you want to add a comment or explanation to the report?"
    await _send_or_edit(event, text, with_nav(comment_buttons(peer=peer)), send=send)


async def show_message_reason_step(event, data: dict, send: bool = True):
    """Load the exact first page of report reasons from Telegram."""
    user_id = event.sender_id
    target = data.get("target") or ""
    messages = data.get("messages") or []
    if not messages:
        await _send_or_edit(
            event,
            "No message link is available for this report. Start again.",
            nav_buttons(),
            send=send,
        )
        return

    progress = await _send_or_edit(
        event,
        "Loading the exact report reasons from Telegram...",
        send=send,
    )
    result = await load_report_choices(None, target, messages[0], option_bytes=b"")

    if result.get("kind") == "choose" and result.get("options"):
        data["reason_pages"] = [result["options"]]
        data["reason_page"] = 0
        data["reason"] = ""
        data["reason_detail"] = ""
        data["reason_option"] = ""
        data["reason_pending_option"] = ""
        data["comment_required"] = False
        db.set_user_state(user_id, "report_reason", data)
        text = (
            "**Step 6/6 - Report Reason**\n\n"
            f"Target: `{target}`\n\n"
            "These are the exact report reasons returned by Telegram. Choose "
            "one reason. If Telegram returns more options, they are shown next."
        )
        buttons = report_option_buttons(result["options"], "reason_opt")
        buttons.append([Button.inline("Try Again", b"reason_retry")])
        buttons.append([Button.inline("Main Menu", b"menu:home")])
        try:
            await progress.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)
        return

    error = result.get("error") or "Telegram did not return any report reasons."
    db.set_user_state(user_id, "report_reason", data)
    text = (
        "**Telegram Report Reasons Unavailable**\n\n"
        f"{error}\n\n"
        "The bot only shows reasons returned by Telegram. Retry when the "
        "account is connected."
    )
    buttons = [
        [Button.inline("Try Again", b"reason_retry")],
        [Button.inline("Main Menu", b"menu:home")],
    ]
    try:
        await progress.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def show_stored_reason_page(event, data: dict):
    """Show a reason or sub-option page already returned by Telegram."""
    pages = data.get("reason_pages") or []
    page_index = max(0, min(int(data.get("reason_page") or 0), len(pages) - 1))
    page = pages[page_index] if pages else []
    if not page:
        await show_message_reason_step(event, data, send=False)
        return

    if page_index == 0:
        text = (
            "**Step 6/6 - Report Reason**\n\n"
            f"Target: `{data.get('target', '')}`\n\n"
            "These are the exact report reasons returned by Telegram."
        )
    else:
        text = (
            "**Telegram Report Options**\n\n"
            f"Reason: {data.get('reason') or 'selected reason'}\n\n"
            "These are the exact sub-options returned by Telegram."
        )
    data["reason_page"] = page_index
    db.set_user_state(event.sender_id, "report_reason", data)
    buttons = report_option_buttons(page, "reason_opt")
    if page_index > 0:
        buttons.append([Button.inline("Back", b"reason_back")])
    buttons.append([Button.inline("Try Again", b"reason_retry")])
    buttons.append([Button.inline("Main Menu", b"menu:home")])
    await _send_or_edit(event, text, buttons, send=False)


async def show_peer_reason_step(event, data: dict, send: bool = True, state: str = "report_peer_reason"):
    user_id = event.sender_id
    db.set_user_state(user_id, state, data)
    text = (
        "**Select Reason**\n\n"
        f"Target: `{data.get('target', '')}`\n\n"
        "Choose one of the ten real Telegram report reasons. Whole-peer "
        "reports use Telegram's reason type directly."
    )
    await _send_or_edit(event, text, with_nav(peer_reason_buttons()), send=send)


@bot.on(events.NewMessage(pattern='/report'))
async def report_command(event):
    """Handle /report command - Start reporting process."""
    await show_report_start(event, edit=False)


@bot.on(events.NewMessage(pattern='/stop'))
async def stop_command(event):
    """Handle /stop command."""
    user_id = event.sender_id

    if not is_user_authorized(user_id):
        await event.respond("Access denied.")
        return

    if user_id in active_reports:
        engine = active_reports[user_id].get('engine')
        if engine:
            engine.should_stop = True
        await event.respond(
            "Stop request sent.",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
    else:
        await event.respond(
            "No report session is active.",
            buttons=[
                [Button.inline("Start Reporting", b"menu:report")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )


@bot.on(events.NewMessage(pattern='/user'))
async def report_user_command(event):
    """Handle /user command - Start user/bot reporting process."""
    user_id = event.sender_id

    if not is_user_authorized(user_id):
        await event.respond("Access denied.")
        return

    if user_id in active_reports:
        await event.respond("A report session is already running. Use /stop first.")
        return

    buttons = [
        [Button.inline("👤 Report User (Peer)", b"peer_type_user"),
         Button.inline("🖼️ User Profile Media (PFP)", b"pfp_type_user")],
        [Button.inline("🤖 Report Bot", b"peer_type_bot")],
        [Button.inline("Main Menu", b"menu:home")],
    ]

    await event.respond(
        "**Report User / Bot**\n\n"
        "Choose what you want to report:",
        buttons=buttons,
    )

    db.set_user_state(user_id, "report_user_type", {"step": 1})
