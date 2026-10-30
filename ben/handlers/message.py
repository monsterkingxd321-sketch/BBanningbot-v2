import re
import asyncio
from telethon import events, Button
from bot import bot, db, active_operations
from config import MAX_REPORTS_PER_ACCOUNT, MIN_DELAY, MAX_DELAY
from utils.helpers import is_user_authorized, mask_phone, log_activity, SessionManager
from handlers.review import (
    handle_review_channel,
    handle_review_explanation,
    handle_review_interval_custom,
    handle_review_cycles_custom,
    handle_review_search,
    handle_review_import_upload,
)
from handlers.account import begin_login_count
from handlers.campaign import handle_campaign_text
from handlers.report import (
    show_target_type,
    run_scrape_flow,
    show_count_step,
    show_user_count_step,
    show_delay_step,
    show_dist_mode_step,
    show_exec_mode_step,
    show_message_reason_step,
    show_peer_reason_step,
)

@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def handle_messages(event):
    """Handle text messages for multi-step operations"""
    user_id = event.sender_id
    text = (event.raw_text or "").strip()
    
    # Check if we are waiting for OTP or Password for this user (SessionManager)
    if user_id in active_operations:
        if not text:
            await event.respond("Please send the verification code or 2FA password as a text message.")
            return
        if text == '/cancel':
            active_operations[user_id]['data']['cancelled'] = True
            active_operations[user_id]['event'].set()
            db.clear_user_state(user_id)
            await event.respond("Login cancelled.")
            return
        if text.startswith('/'):
            await event.respond(
                "A login/OTP process is still running.\n"
                "Send the OTP or 2FA password, or use `/cancel` to cancel the login.",
                buttons=[[Button.inline("Cancel Login", b"ui_cancel")]],
            )
            return
        state_data = active_operations[user_id]['data']
        # If we expect OTP
        if 'otp'not in state_data and not state_data.get('awaiting_password'):
            state_data['otp'] = text
            active_operations[user_id]['event'].set()
            return
        # If we expect Password
        elif state_data.get('awaiting_password') and 'password'not in state_data:
            state_data['password'] = text
            active_operations[user_id]['event'].set()
            return
    
    if not is_user_authorized(user_id):
        await event.respond("Access Denied!")
        return
        
    state, data = db.get_user_state(user_id)

    # Auto-detect session or ZIP uploads anytime an authorized user sends one
    if event.document and not text.startswith('/'):
        doc_name = (getattr(event.file, "name", None) or "").strip().lower()
        mime = (getattr(event.document, "mime_type", None) or "").lower()
        if (
            state == "review_import_upload"
            or doc_name.endswith(".zip")
            or doc_name.endswith(".session")
            or "zip" in mime
            or "octet-stream" in mime
        ):
            await handle_review_import_upload(event, data or {})
            return

    # Ignore commands
    if text.startswith('/'):
        return
    
    if not state:
        await event.respond(
            "Use commands to interact with the bot.\n"
            "Send /help for available commands."
        )
        return
    
    # Handle different states
    if state == "login_count":
        await handle_login_count(event, data)
    elif state == "login_phone":
        await handle_login_phone(event, data)
    elif state == "report_target":
        await handle_report_target(event, data)
    elif state == "report_scrape_count":
        await handle_report_scrape_count(event, data)
    elif state == "report_messages":
        await handle_report_messages(event, data)
    elif state == "report_count":
        await handle_report_count(event, data)
    elif state == "report_delay":
        await handle_report_delay(event, data)
    elif state == "report_comment":
        await handle_report_comment(event, data)
    elif state == "report_reason":
        await event.respond(
            "Choose one of the report reason buttons above. Telegram decides "
            "which reasons and sub-options are available.",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
    elif state == "report_required_comment":
        data['comment'] = (event.text or "").strip()
        db.set_user_state(user_id, "report_reason", data)
        from handlers.callback import confirm_report
        await confirm_report(event, data.get('reason') or "other")
    elif state == "report_peer_count":
        await handle_report_peer_count(event, data)
    elif state == "report_peer_comment":
        await handle_report_peer_comment(event, data)
    elif state == "remove_account":
        await handle_remove_account(event, data)
    elif state == "broadcast":
        await handle_broadcast(event, data)
    elif state == "report_user_target":
        await handle_report_user_target(event, data)
    elif state == "report_user_count":
        await handle_report_user_count(event, data)
    elif state == "report_user_comment":
        await handle_report_user_comment(event, data)
    elif state == "report_user_pfp_target":
        await handle_report_user_pfp_target(event, data)
    elif state == "report_pfp_custom":
        await handle_report_pfp_custom(event, data)
    elif state == "report_pfp_count":
        await handle_report_pfp_count(event, data)
    elif state == "report_pfp_delay":
        await handle_report_pfp_delay(event, data)
    elif state == "report_pfp_comment":
        await handle_report_pfp_comment(event, data)
    elif state == "users_add_id":
        await handle_users_add_id(event, data)
    elif state == "users_revoke_id":
        await handle_users_revoke_id(event, data)
    elif state == "upload_proxies":
        await handle_upload_proxies(event, data)
    elif state == "review_channel":
        await handle_review_channel(event, data)
    elif state == "review_explanation":
        await handle_review_explanation(event, data)
    elif state == "review_interval_custom":
        await handle_review_interval_custom(event, data)
    elif state == "review_cycles_custom":
        await handle_review_cycles_custom(event, data)
    elif state == "review_search":
        await handle_review_search(event, data)
    elif state == "review_search_active":
        await handle_review_search(event, data)
    elif state == "review_import_preview":
        await event.respond("Use the import buttons above, or send /cancel.")
    elif state == "campaign_flow":
        await handle_campaign_text(event, data)

async def handle_login_count(event, data: dict):
    """Handle login count input."""
    try:
        count = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    await begin_login_count(event, data, count, send=True)

async def handle_login_phone(event, data: dict):
    """Handle phone number and proxy input for login"""
    user_id = event.sender_id
    
    parts = event.text.strip().split()
    if not parts:
        await event.respond("Phone number invalid!")
        return
        
    phone = parts[0]
    
    if not phone.startswith('+'):
        await event.respond("Phone number must start with + and country code!")
        return
    
    proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = None, None, None, None, None
    if len(parts) > 1:
        proxy_str = parts[1]
        try:
            if "://"in proxy_str:
                scheme, rest = proxy_str.split("://", 1)
                proxy_type = scheme.lower()
                if "@"in rest:
                    creds, addr = rest.split("@", 1)
                    proxy_user, proxy_pass = creds.split(":", 1)
                else:
                    addr = rest
                proxy_ip, proxy_port = addr.split(":", 1)
                proxy_port = int(proxy_port)
            else:
                sub_parts = proxy_str.split(":")
                proxy_type = "socks5"
                proxy_ip = sub_parts[0]
                proxy_port = int(sub_parts[1])
                if len(sub_parts) > 3:
                    proxy_user = sub_parts[2]
                    proxy_pass = sub_parts[3]
        except Exception:
            await event.respond("Proxy format invalid! proceeding without proxy...")
            proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = None, None, None, None, None
            
    current = data['current'] + 1
    
    progress_msg = await event.respond(f"Logging in account {current}...")
    
    # Login the account
    session_file = await SessionManager.login_account(
        phone, user_id, progress_msg,
        proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass
    )
    
    if session_file:
        data['phones'].append(phone)
    
    # Stop if the user cancelled or navigated away while the login was running.
    current_state, _ = db.get_user_state(user_id)
    if current_state != "login_phone":
        return
    
    # Check if more accounts to login
    if current < data['total']:
        data['current'] = current
        db.set_user_state(user_id, "login_phone", data)
        
        await event.respond(
            f"**Account {current + 1}/{data['total']}**\n\n"
            f"Send the phone number (format: `[phone] [proxy]`):",
            buttons=[
                [Button.inline("Cancel Login", b"ui_cancel")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )
    else:
        # All accounts logged in
        db.clear_user_state(user_id)
        success_count = len(data['phones'])
        await event.respond(
            f"**Login Finished!**\n\n"
            f"Successful: {success_count}/{current}\n"
            f"Phones: {', '.join([mask_phone(p) for p in data['phones']]) or 'none'}",
            buttons=[
                [Button.inline("My Accounts", b"menu:accounts"),
                 Button.inline("Start Reporting", b"menu:report")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )
        log_activity(f"User {user_id} logged in {success_count}/{current} accounts")

async def handle_report_target(event, data: dict):
    """Handle report target input."""
    target = (event.text or "").strip()
    if not target:
        await event.respond("The target is empty. Send a channel/group username or link.")
        return

    clean_target = target.split("?")[0].split("#")[0].strip()
    is_msg_link = ("/c/" in clean_target or bool(re.search(r"t\.me/[^/]+/\d+", clean_target)))
    if is_msg_link:
        data['target'] = target
        data['messages'] = [target]
        await event.respond(
            f"**Message Link Detected**\n\n"
            f"Target message: `{target}`\n\n"
            f"Do you want to report this message directly, or configure options?",
            buttons=[
                [Button.inline("⚡ Report This Message Directly", b"direct_msg_report")],
                [Button.inline("⚙️ Choose Target Options", b"target_options")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )
        db.set_user_state(event.sender_id, "report_target", data)
        return

    data['target'] = target
    await show_target_type(event, data, send=True)

async def handle_report_scrape_count(event, data: dict):
    """Handle number of messages to auto-scrape."""
    try:
        limit = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    await run_scrape_flow(event, data, limit, edit=False)

async def handle_report_messages(event, data: dict):
    """Handle message links or custom message numbers input manually."""
    raw_text = (event.text or "").strip()
    lines = raw_text.replace(",", "\n").split("\n")
    valid_links = []
    target = data.get('target', '').strip()
    clean_target = target.replace("https://", "").replace("http://", "").replace("t.me/", "").replace("@", "").split("/")[0].split("?")[0]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("https://t.me/") or line.startswith("t.me/") or line.startswith("tg://"):
            if line.startswith("t.me/"):
                line = "https://" + line
            valid_links.append(line)
        elif "-" in line and clean_target:
            sub = line.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start_m, end_m = int(sub[0]), int(sub[1])
                for m_id in range(min(start_m, end_m), max(start_m, end_m) + 1):
                    if target.startswith("-100") or "/c/" in target:
                        c_id = target.replace("-100", "").replace("https://t.me/c/", "").split("/")[0]
                        valid_links.append(f"https://t.me/c/{c_id}/{m_id}")
                    else:
                        valid_links.append(f"https://t.me/{clean_target}/{m_id}")
        elif line.isdigit() and clean_target:
            m_id = int(line)
            if target.startswith("-100") or "/c/" in target:
                c_id = target.replace("-100", "").replace("https://t.me/c/", "").split("/")[0]
                valid_links.append(f"https://t.me/c/{c_id}/{m_id}")
            else:
                valid_links.append(f"https://t.me/{clean_target}/{m_id}")

    if not valid_links:
        await event.respond(
            "No valid message links or IDs found!\n\n"
            "Examples:\n"
            "- `https://t.me/channel/123`\n"
            "- `https://t.me/c/1234567890/123`\n"
            "- `120-125` (range of message IDs)\n"
            "- `123, 125, 128` (specific message IDs)",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
        return

    data['messages'] = valid_links
    await show_count_step(
        event,
        data,
        send=True,
        state="report_count",
        max_count=MAX_REPORTS_PER_ACCOUNT,
        title="Step 3/6 - Reports Per Account",
        prefix=f"✅ {len(valid_links)} message link(s) accepted.\n\n",
    )

async def handle_report_count(event, data: dict):
    """Handle report count input."""
    try:
        count = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if count < 1 or count > MAX_REPORTS_PER_ACCOUNT:
        await event.respond(f"Number must be between 1 and {MAX_REPORTS_PER_ACCOUNT}")
        return
    data['reports_per_account'] = count
    await show_delay_step(event, data, send=True)

async def handle_report_delay(event, data: dict):
    """Handle delay input."""
    try:
        delay = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if delay < MIN_DELAY or delay > MAX_DELAY:
        await event.respond(f"Delay must be between {MIN_DELAY} and {MAX_DELAY} seconds")
        return
    data['delay'] = delay
    if len(data.get('messages', [])) > 1:
        await show_dist_mode_step(event, data, send=True)
    else:
        data['dist_mode'] = 'round_robin'
        await show_exec_mode_step(event, data, send=True, peer=False)

async def handle_report_comment(event, data: dict):
    """Handle comment text input."""
    data['comment'] = (event.text or "").strip()
    await show_message_reason_step(event, data, send=True)

async def handle_remove_account(event, data: dict):
    """Handle remove account input"""
    user_id = event.sender_id
    
    phone = event.text.strip()
    
    if db.remove_account(phone):
        await event.respond(
            f"Account {mask_phone(phone)} removed!",
            buttons=[[Button.inline("My Accounts", b"menu:accounts")],
                     [Button.inline("Main Menu", b"menu:home")]],
        )
    else:
        await event.respond(
            f"Account {phone} not found!",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
    
    db.clear_user_state(user_id)

async def handle_broadcast(event, data: dict):
    """Handle broadcast message"""
    user_id = event.sender_id
    
    broadcast_msg = event.text
    users = db.get_all_approved_users()
    
    sent_count = 0
    failed_count = 0
    
    progress_msg = await event.respond("Broadcasting...")
    
    for user in users:
        try:
            await bot.send_message(user[0], broadcast_msg)
            sent_count += 1
            await asyncio.sleep(0.5)  # Rate limiting
        except Exception as e:
            failed_count += 1
            log_activity(f"Broadcast failed for {user[0]}: {str(e)}")
    
    await progress_msg.edit(
        f"**Broadcast Complete**\n\n"
        f"Sent: {sent_count}\n"
        f"Failed: {failed_count}\n"
        f"Total Users: {len(users)}",
        buttons=[[Button.inline("Main Menu", b"menu:home")]],
    )
    
    db.clear_user_state(user_id)

async def handle_report_user_target(event, data: dict):
    """Handle user/bot target input."""
    data['target'] = (event.text or "").strip()
    await show_user_count_step(event, data, send=True)

async def handle_report_user_count(event, data: dict):
    """Handle reports count input for user/bot."""
    try:
        count = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if count < 1 or count > MAX_REPORTS_PER_ACCOUNT:
        await event.respond(f"Number must be between 1 and {MAX_REPORTS_PER_ACCOUNT}")
        return

    data['reports_per_account'] = count

    if data['type'] == 'bot':
        db.set_user_state(event.sender_id, "report_user_comment", data)
        await event.respond(
            "**Comment**\n\nSend the comment or explanation for the bot report:",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
    else:
        await show_peer_reason_step(event, data, send=True, state="report_user_reason")

async def handle_report_user_comment(event, data: dict):
    """Handle comment input for bot."""
    data['comment'] = (event.text or "").strip()
    await show_peer_reason_step(event, data, send=True, state="report_user_reason")

async def handle_report_peer_count(event, data: dict):
    """Handle peer report count input."""
    try:
        count = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if count < 1 or count > MAX_REPORTS_PER_ACCOUNT:
        await event.respond(f"Number must be between 1 and {MAX_REPORTS_PER_ACCOUNT}")
        return
    data['reports_per_account'] = count
    await show_exec_mode_step(event, data, send=True, peer=True)

async def handle_report_peer_comment(event, data: dict):
    """Handle peer report comment text input."""
    data['comment'] = (event.text or "").strip()
    await show_peer_reason_step(event, data, send=True, state="report_peer_reason")

async def handle_upload_proxies(event, data: dict):
    """Handle proxy list text or document upload"""
    user_id = event.sender_id
    
    proxy_content = ""
    if event.document:
        progress_msg = await event.respond("Downloading the proxy file...")
        try:
            file_bytes = await event.download_media(bytes)
            proxy_content = file_bytes.decode('utf-8', errors='ignore')
            await progress_msg.delete()
        except Exception as e:
            await event.respond(f"Error while downloading the file: {str(e)}")
            return
    else:
        proxy_content = event.text.strip()
        
    lines = proxy_content.splitlines()
    parsed_proxies = []
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
            
        proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = None, None, None, None, None
        try:
            if "://"in line:
                scheme, rest = line.split("://", 1)
                proxy_type = scheme.lower()
                if "@"in rest:
                    creds, addr = rest.split("@", 1)
                    proxy_user, proxy_pass = creds.split(":", 1)
                else:
                    addr = rest
                proxy_ip, proxy_port = addr.split(":", 1)
                proxy_port = int(proxy_port)
            else:
                sub_parts = line.split(":")
                proxy_type = "socks5"
                proxy_ip = sub_parts[0]
                proxy_port = int(sub_parts[1])
                if len(sub_parts) > 3:
                    proxy_user = sub_parts[2]
                    proxy_pass = sub_parts[3]
                    
            if proxy_ip and proxy_port:
                parsed_proxies.append((proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass))
        except Exception:
            continue
            
    if not parsed_proxies:
        await event.respond("No valid proxy was found. Check the format and try again.")
        return
        
    accounts = db.get_accounts()
    if not accounts:
        await event.respond("No registered accounts are available for proxy assignment.")
        db.clear_user_state(user_id)
        return
        
    assigned_count = 0
    num_proxies = len(parsed_proxies)
    
    for idx, acc in enumerate(accounts):
        phone = acc[1]
        p_type, p_ip, p_port, p_user, p_pass = parsed_proxies[idx % num_proxies]
        db.update_account_proxy(phone, p_type, p_ip, p_port, p_user, p_pass)
        assigned_count += 1
        
    await event.respond(
        f"**Proxies Assigned**\n\n"
        f"Parsed proxies: `{len(parsed_proxies)}`\n"
        f"Bound accounts: `{assigned_count}`"
    )
    
    db.clear_user_state(user_id)

async def handle_users_add_id(event, data: dict):
    user_id = event.sender_id
    text = (event.text or "").strip()
    try:
        target_id = int(text)
    except ValueError:
        await event.respond("Please enter a valid numeric Telegram User ID!\nExample: `1234567890`")
        return
    if db.approve_user(target_id, "Approved User", user_id):
        db.clear_user_state(user_id)
        await event.respond(
            f"✅ **User Approved Successfully!**\n\n"
            f"User ID: `{target_id}`\n"
            f"This user can now access and use the bot.",
            buttons=[
                [Button.inline("➕ Approve Another", b"users:add"),
                 Button.inline("Approved Users", b"menu:users")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )
        log_activity(f"User {target_id} approved by admin {user_id}")
    else:
        await event.respond("Failed to approve user. Try again.")

async def handle_users_revoke_id(event, data: dict):
    user_id = event.sender_id
    text = (event.text or "").strip()
    try:
        target_id = int(text)
    except ValueError:
        await event.respond("Please enter a valid numeric Telegram User ID!\nExample: `1234567890`")
        return
    if db.revoke_user(target_id):
        db.clear_user_state(user_id)
        await event.respond(
            f"❌ **User Access Revoked!**\n\n"
            f"User ID: `{target_id}` is no longer authorized to use this bot.",
            buttons=[
                [Button.inline("Approved Users", b"menu:users")],
                [Button.inline("Main Menu", b"menu:home")],
            ],
        )
        log_activity(f"User {target_id} revoked by admin {user_id}")
    else:
        await event.respond("Failed to revoke user. Try again.")

async def handle_report_user_pfp_target(event, data: dict):
    target = (event.text or "").strip()
    if not target:
        await event.respond("Please send a valid username or user ID!")
        return
    data['target'] = target
    from handlers.report import show_pfp_mode_step
    await show_pfp_mode_step(event, data, send=True)

async def handle_report_pfp_custom(event, data: dict):
    text = (event.text or "").strip()
    indices = []
    parts = text.replace(" ", "").split(",")
    for part in parts:
        if "-" in part:
            sub = part.split("-")
            if len(sub) == 2 and sub[0].isdigit() and sub[1].isdigit():
                start_i, end_i = int(sub[0]), int(sub[1])
                for idx in range(min(start_i, end_i), max(start_i, end_i) + 1):
                    if idx > 0 and idx not in indices:
                        indices.append(idx)
        elif part.isdigit():
            idx = int(part)
            if idx > 0 and idx not in indices:
                indices.append(idx)
    if not indices:
        await event.respond(
            "Please send valid photo/video number(s)!\n"
            "Examples:\n- `1` (Current/first profile picture)\n- `1, 2` (1st and 2nd)\n- `1-3` (first 3)",
            buttons=[[Button.inline("Main Menu", b"menu:home")]],
        )
        return
    data['pfp_indices'] = indices
    data['pfp_mode'] = 'custom'
    from handlers.report import show_pfp_count_step
    await show_pfp_count_step(event, data, send=True)

async def handle_report_pfp_count(event, data: dict):
    try:
        count = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if count < 1 or count > MAX_REPORTS_PER_ACCOUNT:
        await event.respond(f"Number must be between 1 and {MAX_REPORTS_PER_ACCOUNT}")
        return
    data['reports_per_account'] = count
    db.set_user_state(event.sender_id, "report_pfp_delay", data)
    from handlers.report import show_delay_step
    await show_delay_step(event, data, send=True)

async def handle_report_pfp_delay(event, data: dict):
    try:
        delay = int((event.text or "").strip())
    except ValueError:
        await event.respond("Please enter a valid number!")
        return
    if delay < MIN_DELAY or delay > MAX_DELAY:
        await event.respond(f"Delay must be between {MIN_DELAY} and {MAX_DELAY} seconds")
        return
    data['delay'] = delay
    from handlers.report import show_exec_mode_step
    await show_exec_mode_step(event, data, send=True, peer=True)

async def handle_report_pfp_comment(event, data: dict):
    data['comment'] = (event.text or "").strip()
    from handlers.report import show_peer_reason_step
    await show_peer_reason_step(event, data, send=True, state="report_pfp_reason")

@bot.on(events.NewMessage(pattern='/cancel'))
async def cancel_command(event):
    """Handle /cancel command"""
    user_id = event.sender_id
    state, _ = db.get_user_state(user_id)
    if state and state.startswith("review_"):
        from handlers.review import cancel_review_state
        cancel_review_state(user_id)
    else:
        db.clear_user_state(user_id)
    await event.respond(
        "Operation cancelled.",
        buttons=[[Button.inline("Main Menu", b"menu:home")]],
    )
