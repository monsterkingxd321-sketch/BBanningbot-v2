import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from telethon import TelegramClient, types, utils, errors as tg_errors
from telethon.tl.functions.messages import ReportRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.account import ReportPeerRequest, UpdateStatusRequest, ReportProfilePhotoRequest
from config import LOGS_FOLDER, SESSIONS_FOLDER, API_ID, API_HASH
from bot import db, active_operations
from utils.review_utils import redact_sensitive
from utils.message_utils import is_reportable_post

def mask_phone(phone: str) -> str:
    """Return full phone number without masking (per user request)."""
    return str(phone) if phone else "unknown"

def create_progress_bar(percentage: float, length: int = 10) -> str:
    """Create a plain-text progress bar."""
    filled = int(length * percentage / 100)
    bar = "#" * filled + "-" * (length - filled)
    return f"[{bar}] {percentage:.1f}%"

def log_activity(message: str):
    """Log activities to file, redacting phone numbers and tokens."""
    try:
        os.makedirs(LOGS_FOLDER, exist_ok=True)
        log_file = os.path.join(LOGS_FOLDER, f"bot_log_{datetime.now().strftime('%Y%m%d')}.txt")
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        safe_message = redact_sensitive(message, 1000)
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {safe_message}\n")
    except Exception:
        pass

def is_user_authorized(user_id: int) -> bool:
    """Check if user is authorized (admin or approved)"""
    return db.is_admin(user_id) or db.is_approved(user_id)

def get_user_status(user_id: int) -> str:
    """Get user status"""
    if db.is_admin(user_id):
        return "admin"
    elif db.is_approved(user_id):
        return "approved"
    else:
        return "unauthorized"

def get_telethon_proxy(proxy_type: str, proxy_ip: str, proxy_port: int, proxy_user: str = None, proxy_pass: str = None):
    """Parse proxy configuration to Telethon format with DNS pre-resolution"""
    if not proxy_ip or not proxy_port:
        return None
    try:
        import socks
        import socket
        t_map = {
            'socks5': socks.SOCKS5,
            'socks4': socks.SOCKS4,
            'http': socks.HTTP
        }
        ptype = t_map.get(str(proxy_type or 'socks5').lower().strip(), socks.SOCKS5)
        ip_clean = str(proxy_ip).strip()
        if "://"in ip_clean:
            ip_clean = ip_clean.split("://", 1)[1]
            
        # Pre-resolve proxy hostname to plain IP to prevent gaierror [Errno 11001]
        try:
            resolved_ip = socket.gethostbyname(ip_clean)
        except Exception:
            # Fallback to original clean IP if resolution fails (e.g. offline)
            resolved_ip = ip_clean
            
        return (ptype, resolved_ip, int(proxy_port), True, (proxy_user or '').strip() or None, (proxy_pass or '').strip() or None)
    except Exception as e:
        log_activity(f"Proxy parsing error: {e}")
        return None

async def set_account_online(client: TelegramClient, offline: bool = False):
    """Set the Telegram account status to online (offline=False) or offline (offline=True)."""
    try:
        if client and client.is_connected():
            await client(UpdateStatusRequest(offline=offline))
    except Exception:
        pass

def resolve_session_path(session_file: str, phone: str = None) -> Optional[str]:
    """Resolve the actual path to the .session file across platforms (Windows / Linux / Docker / Cloud).
    Handles cases where the database stored an absolute path from another OS (e.g. F:\\... on Linux or /app/... on Windows).
    If local session is missing, attempts to restore it from MongoDB Atlas if session_data is available.
    Returns the valid filepath or None.
    """
    if session_file and os.path.isfile(session_file):
        return session_file

    clean_phone = (phone or "").replace("+", "").strip()

    if session_file:
        fname = os.path.basename(session_file.replace("\\", "/"))
        candidate = os.path.join(SESSIONS_FOLDER, fname)
        if os.path.isfile(candidate):
            return candidate

    if clean_phone:
        candidate = os.path.join(SESSIONS_FOLDER, f"account_{clean_phone}.session")
        if os.path.isfile(candidate):
            return candidate

    # Try restoring from MongoDB Atlas if available
    try:
        if db and getattr(db, "mongo_db", None) is not None and phone:
            mongo_acc = db.mongo_db.accounts.find_one({"phone": phone})
            if mongo_acc and mongo_acc.get("session_data"):
                import base64
                os.makedirs(SESSIONS_FOLDER, exist_ok=True)
                dest = os.path.join(SESSIONS_FOLDER, f"account_{clean_phone}.session")
                with open(dest, "wb") as sf:
                    sf.write(base64.b64decode(mongo_acc["session_data"]))
                if os.path.isfile(dest):
                    return dest
    except Exception as exc:
        log_activity(f"Failed to restore session for {phone} from MongoDB: {exc}")

    return None

def parse_message_link(message_link: str) -> tuple:
    """Return ``(chat_reference, message_id)`` for a Telegram message link.

    Public links use the channel username. Private ``/c/`` links use the numeric
    channel id. ``tg://privatepost`` links are accepted as well.
    """
    raw = (message_link or "").strip()
    if not raw:
        raise ValueError("Message link is empty.")

    clean_raw = raw.split("?")[0].split("#")[0].strip()

    if clean_raw.lower().startswith("tg://privatepost"):
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(raw).query)
        channel = (query.get("channel") or [""])[0]
        post = (query.get("post") or [""])[0]
        if channel and post:
            return int(f"-100{channel}"), int(post)
        raise ValueError("Private post link is invalid.")

    if "/c/" in clean_raw:
        parts = clean_raw.split("/c/", 1)
        sub_parts = parts[1].strip("/").split("/")
        if len(sub_parts) >= 2 and sub_parts[0].isdigit() and sub_parts[-1].isdigit():
            return int(f"-100{sub_parts[0]}"), int(sub_parts[-1])
        raise ValueError("Private message link is invalid.")

    clean_link = clean_raw.replace("https://", "").replace("http://", "")
    clean_link = clean_link.replace("t.me/", "").replace("telegram.me/", "")
    parts = clean_link.strip("/").split("/")
    if len(parts) >= 2:
        chat_ref = parts[0]
        if chat_ref.startswith("@"):
            chat_ref = chat_ref[1:]
        if parts[-1].isdigit():
            return chat_ref, int(parts[-1])
    raise ValueError("Message link is invalid.")


def parse_target_identifier(target: str) -> tuple:
    """Parse a public username/link or a private invite link.

    Returns ``("username", value)``, ``("id", value)`` or ``("invite", hash)``.
    Message links such as ``https://t.me/channel/123`` are treated as the
    channel part so they cannot accidentally be reported as a target.
    """
    raw = (target or "").strip()
    if not raw:
        raise ValueError("Target is empty.")

    value = raw
    if value.lower().startswith("tg://join?invite="):
        invite = value.split("invite=", 1)[1].split("&", 1)[0]
        if invite:
            return "invite", invite
    if "://"in value:
        value = value.split("://", 1)[1]
    if value.lower().startswith(("t.me/", "telegram.me/")):
        value = value.split("/", 1)[1]
    value = value.split("?", 1)[0].strip("/")

    if value.startswith("joinchat/"):
        invite = value.split("/", 1)[1]
        if not invite:
            raise ValueError("Invite link has no hash.")
        return "invite", invite
    if value.startswith("+"):
        invite = value[1:].split("/", 1)[0]
        if not invite:
            raise ValueError("Invite link has no hash.")
        return "invite", invite
    if value.startswith("@"):
        value = value[1:]
    if value.startswith("c/"):
        sub = value.split("c/", 1)[1].strip("/").split("/")[0]
        if sub.isdigit():
            return "id", int(f"-100{sub}")
        raise ValueError("Private message link has invalid channel ID.")

    # For t.me/channel/123, the join target is the channel username.
    value = value.split("/", 1)[0]
    if not value:
        raise ValueError("Target is invalid.")
    try:
        return "id", int(value)
    except ValueError:
        return "username", value


async def ensure_account_joined(client: TelegramClient, target: str) -> tuple:
    """Join *target* with *client* and return ``(joined, message)``.

    ``UserAlreadyParticipantError`` means the account is already a member and is
    therefore a success. A pending join request is intentionally treated as a
    failure because the account cannot report on the target yet.
    """
    kind, value = parse_target_identifier(target)

    async def _join_once():
        if kind == "invite":
            await client(ImportChatInviteRequest(value))
            return
        entity = value
        if kind == "id":
            try:
                await client.get_entity(value)
                return
            except Exception:
                try:
                    await client(JoinChannelRequest(value))
                    return
                except Exception:
                    pass
        elif kind == "username":
            try:
                entity = await client.get_entity(value)
                if isinstance(entity, types.User):
                    return
            except Exception:
                # Joining by username can still work when entity resolution is
                # temporarily unavailable.
                entity = value
        await client(JoinChannelRequest(entity))

    for attempt in range(2):
        try:
            await _join_once()
            return True, "joined"
        except tg_errors.UserAlreadyParticipantError:
            return True, "already joined"
        except tg_errors.InviteRequestSentError:
            return False, "join request sent; admin approval is pending"
        except (tg_errors.InviteHashExpiredError, tg_errors.InviteHashInvalidError):
            return False, "invite link is expired or invalid"
        except tg_errors.FloodWaitError as exc:
            if attempt == 0 and exc.seconds <= 15:
                await asyncio.sleep(exc.seconds)
                continue
            return False, f"Telegram flood wait ({exc.seconds}s)"
        except Exception as exc:
            message = redact_sensitive(exc, 200)
            if attempt == 0 and "already"in message.lower():
                return True, "already joined"
            return False, message
    return False, "could not join target"


async def join_accounts_to_target(target: str, progress_message=None, accounts: list = None) -> list:
    """Join every active account to *target* before reporting.

    Returns a list of dictionaries with ``phone``, ``joined`` and ``message``.
    The function never raises for an individual account; the caller can use the
    joined phone numbers as an allow-list for the report engine.
    """
    active_accounts = list(accounts if accounts is not None else db.get_active_accounts())
    results = []
    if not active_accounts:
        return results

    total = len(active_accounts)
    joined_count = 0
    failed_count = 0

    for index, account in enumerate(active_accounts, start=1):
        phone, session_file = account[1], account[2]
        proxy_type, proxy_ip, proxy_port = account[4], account[5], account[6]
        proxy_user, proxy_pass = account[7], account[8]

        if progress_message is not None:
            try:
                await progress_message.edit(
                    "**Joining accounts to target**\n\n"
                    f"Progress: {index}/{total}\n"
                    f"Current: `{mask_phone(phone)}`\n\n"
                    f"Joined: {joined_count}\n"
                    f"Failed: {failed_count}"
                )
            except Exception:
                pass

        client = None
        ok = False
        message = ""
        try:
            session_path = resolve_session_path(session_file, phone)
            if not session_path:
                message = "session file is missing"
            else:
                proxy_tuple = get_telethon_proxy(
                    proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass
                )
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(
                    base_session, API_ID, API_HASH, proxy=proxy_tuple
                )
                await client.connect()
                if not await client.is_user_authorized():
                    message = "session is not authorized"
                    try:
                        db.update_account_status(phone, "error", False)
                    except Exception:
                        pass
                else:
                    ok, message = await ensure_account_joined(client, target)
        except Exception as exc:
            message = redact_sensitive(exc, 200)
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

        if ok:
            joined_count += 1
        else:
            failed_count += 1
            log_activity(f"Join failed for {mask_phone(phone)} on {target}: {message}")

        results.append({"phone": phone, "joined": ok, "message": message})
        await asyncio.sleep(0.25)

    if progress_message is not None:
        try:
            failed_names = ", ".join(
                mask_phone(item["phone"]) for item in results if not item["joined"]
            )
            text = (
                "**Join check complete**\n\n"
                f"Joined: {joined_count}/{total}\n"
                f"Failed: {failed_count}/{total}"
            )
            if failed_names:
                text += f"\n\nFailed accounts: {failed_names[:300]}"
            await progress_message.edit(text)
        except Exception:
            pass

    return results


class SessionManager:
    @staticmethod
    async def login_account(phone: str, user_id: int, progress_message, proxy_type: str = None, proxy_ip: str = None, proxy_port: int = None, proxy_user: str = None, proxy_pass: str = None) -> Optional[str]:
        """Login a Telegram account and return session file path."""
        client = None
        try:
            # Create unique session name
            session_name = f"{SESSIONS_FOLDER}/account_{phone.replace('+', '')}"
            
            # Setup proxy
            proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
            
            client = TelegramClient(session_name, API_ID, API_HASH, proxy=proxy_tuple)
            
            await client.connect()
            
            if not await client.is_user_authorized():
                sent_code = await client.send_code_request(phone)
                phone_code_hash = getattr(sent_code, "phone_code_hash", None)

                login_buttons = [
                    [Button.inline("🔄 Resend OTP (SMS)", b"login_resend_otp"),
                     Button.inline("❌ Cancel Login", b"ui_cancel")],
                ]
                await progress_message.edit(
                    f"**Telegram Login: {mask_phone(phone)}**\n\n"
                    f"📲 **OTP sent successfully!**\n"
                    f"Check Telegram or SMS on your phone.\n\n"
                    f"Send the verification code as a message here,\n"
                    f"or tap **Resend OTP** if you didn't receive it.",
                    buttons=login_buttons
                )
                
                event = asyncio.Event()
                active_operations[user_id] = {
                    'event': event,
                    'data': {'phone_code_hash': phone_code_hash},
                    'client': client,
                    'phone': phone,
                    'progress_message': progress_message,
                }
                
                from telethon.errors import (
                    SessionPasswordNeededError,
                    PhoneCodeInvalidError,
                    PhoneCodeExpiredError,
                    PasswordHashInvalidError
                )

                while True:
                    await event.wait()
                    op_data = active_operations.get(user_id, {}).get('data', {})
                    if op_data.get('cancelled'):
                        raise ValueError("Login cancelled by user.")
                    
                    otp = (op_data.get('otp') or '').strip()
                    if not otp:
                        raise ValueError("No verification code received.")
                    
                    try:
                        await client.sign_in(phone, code=otp)
                        break
                    except PhoneCodeInvalidError:
                        event.clear()
                        op_data.pop('otp', None)
                        await progress_message.edit(
                            f"**Telegram Login: {mask_phone(phone)}**\n\n"
                            f"❌ **Invalid verification code!**\n\n"
                            f"Please re-check and send the correct code,\n"
                            f"or tap **Resend OTP** below.",
                            buttons=login_buttons
                        )
                    except PhoneCodeExpiredError:
                        event.clear()
                        op_data.pop('otp', None)
                        await client.send_code_request(phone)
                        await progress_message.edit(
                            f"**Telegram Login: {mask_phone(phone)}**\n\n"
                            f"⚠️ **Code expired! A new OTP was sent.**\n\n"
                            f"Please send the new verification code:",
                            buttons=login_buttons
                        )
                    except SessionPasswordNeededError:
                        event.clear()
                        op_data['awaiting_password'] = True
                        two_fa_buttons = [[Button.inline("❌ Cancel Login", b"ui_cancel")]]
                        await progress_message.edit(
                            f"**Telegram Login: {mask_phone(phone)}**\n\n"
                            f"🔐 **Two-Step Verification (2FA) is enabled!**\n\n"
                            f"Please send your 2FA password as a text message:",
                            buttons=two_fa_buttons
                        )
                        while True:
                            await event.wait()
                            if op_data.get('cancelled'):
                                raise ValueError("Login cancelled by user.")
                            password = (op_data.get('password') or '').strip()
                            if not password:
                                raise ValueError("No 2FA password received.")
                            try:
                                await client.sign_in(password=password)
                                break
                            except PasswordHashInvalidError:
                                event.clear()
                                op_data.pop('password', None)
                                await progress_message.edit(
                                    f"**Telegram Login: {mask_phone(phone)}**\n\n"
                                    f"❌ **Incorrect 2FA password!**\n\n"
                                    f"Please send the correct password:",
                                    buttons=two_fa_buttons
                                )
                        break
                
            # Save session
            session_file = f"{session_name}.session"
            try:
                os.chmod(session_file, 0o600)
            except OSError:
                pass
            db.add_account(phone, session_file, user_id, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
            
            await progress_message.edit(
                f"Account login successful!\n"
                f"Phone: {mask_phone(phone)}"
            )
            
            log_activity(f"Account {mask_phone(phone)} logged in by user {user_id}")
            return session_file
            
        except Exception as e:
            error = redact_sensitive(e, 300)
            await progress_message.edit(f"Login failed for {mask_phone(phone)}: {error}")
            log_activity(f"Login error for {mask_phone(phone)}: {error}")
            return None
        finally:
            active_operations.pop(user_id, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass

async def scrape_recent_messages(target: str, limit: int = 10, phone: str = None) -> list:
    """Connect using an active account and scrape recent messages from a target."""
    active_accounts = db.get_active_accounts()
    if not active_accounts:
        raise Exception("No active accounts available to scrape target!")

    accounts_to_try = []
    if phone:
        matching = [acc for acc in active_accounts if acc[1] == phone]
        if not matching:
            raise Exception(f"Selected account {mask_phone(phone)} is not active or was not found.")
        accounts_to_try = matching
    else:
        accounts_to_try = list(active_accounts)

    last_error = None
    for account in accounts_to_try:
        acc_phone = account[1]
        session_file = account[2]
        proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account[4], account[5], account[6], account[7], account[8]

        session_path = resolve_session_path(session_file, acc_phone)
        if not session_path:
            last_error = Exception(f"Session file for account {mask_phone(acc_phone)} is missing.")
            continue

        proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
        base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
        client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                last_error = Exception(f"Account {mask_phone(acc_phone)} session is unauthorized or revoked.")
                continue

            kind, value = parse_target_identifier(target)
            entity = None

            if kind == "invite":
                from telethon.tl.functions.messages import CheckChatInviteRequest
                try:
                    invite_result = await client(ImportChatInviteRequest(value))
                    entity = invite_result.chats[0]
                except tg_errors.UserAlreadyParticipantError:
                    check = await client(CheckChatInviteRequest(value))
                    entity = getattr(check, 'chat', None)
                    if entity is None:
                        raise Exception("Invite is valid but the account is not a member yet.")
            else:
                joined, join_message = await ensure_account_joined(client, target)
                if not joined:
                    raise Exception(join_message)
                entity = await client.get_entity(value)

            messages = []
            grouped_seen = set()
            async for msg in client.iter_messages(entity):
                if len(messages) >= limit:
                    break
                if not is_reportable_post(msg, grouped_seen):
                    continue
                if getattr(entity, 'username', None):
                    link = f"https://t.me/{entity.username}/{msg.id}"
                else:
                    raw_id = str(entity.id)
                    if raw_id.startswith("-100"):
                        raw_id = raw_id[4:]
                    elif raw_id.startswith("-"):
                        raw_id = raw_id[1:]
                    link = f"https://t.me/c/{raw_id}/{msg.id}"
                messages.append(link)

            return messages
        except Exception as exc:
            last_error = exc
            log_activity(f"Scrape attempt with {mask_phone(acc_phone)} failed: {exc}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    if last_error:
        raise last_error
    raise Exception("No active account was able to fetch messages.")

async def get_target_profile_photos(target: str, limit: int = 10) -> list:
    """Fetch profile photos for target using an active connected account."""
    active_accounts = db.get_active_accounts()
    if not active_accounts:
        return []
    for account in active_accounts:
        phone, session_file = account[1], account[2]
        session_path = resolve_session_path(session_file, phone)
        if not session_path:
            continue
        proxy_tuple = get_telethon_proxy(account[4], account[5], account[6], account[7], account[8])
        base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
        client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                continue
            s = str(target).strip()
            if s.startswith('@'):
                s = s[1:]
            if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                try:
                    entity = await client.get_entity(int(s))
                except Exception:
                    from telethon.tl.types import PeerUser
                    entity = await client.get_entity(PeerUser(int(s)))
            else:
                entity = await client.get_entity(s)
            photos = await client.get_profile_photos(entity, limit=limit)
            return list(photos or [])
        except Exception as e:
            log_activity(f"Error fetching profile photos with {phone}: {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
    return []

class ReportEngine:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.should_stop = False
    
    async def report_message(
        self, client: TelegramClient, target: str, message_link: str,
        reason: str, comment: str = "", reason_option: str = ""
    ) -> bool:
        """Report one message using Telegram's current option-based flow.

        ``reason_option`` is the real option selected from Telegram's report
        screens. When it is absent, the serialized reason type is used as the
        first option and Telegram's continuation response is followed.
        """
        try:
            try:
                chat_id, message_id = parse_message_link(message_link)
            except ValueError:
                log_activity(f"Failed to parse link: {message_link}")
                return False

            try:
                entity = await self.resolve_entity(client, chat_id)
            except Exception:
                try:
                    entity = await self.resolve_entity(client, target)
                except Exception as e:
                    log_activity(f"Failed to get entity for {chat_id}: {str(e)}")
                    return False

            # Official Telegram option bytes for ReportRequest (Layer 158+)
            TELEGRAM_OPTION_MAP = {
                "child_abuse": b"2",       # 32 (Child abuse)
                "violence": b"3",          # 33 (Violence)
                "illegal_drugs": b"42",    # 3432 (Drugs)
                "illegal_activities": b"4",# 34 (Illegal goods and services)
                "pornography": b"57",      # 3537 (Pornography)
                "fake": b"7",              # 37 (Scam or fraud)
                "scam": b"7",              # 37 (Scam or fraud)
                "personal_details": b"6",  # 36 (Personal data)
                "copyright": b"8",         # 38 (Copyright)
                "spam": b"9",              # 39 (Spam)
                "other": b"a",             # 61 (Other)
                "geo_irrelevant": b"b",    # 62 (It's not illegal, but must be taken down)
            }

            if reason_option:
                try:
                    option = bytes.fromhex(reason_option)
                except Exception:
                    option = TELEGRAM_OPTION_MAP.get(reason, b"9")
            else:
                option = TELEGRAM_OPTION_MAP.get(reason, b"9")

            # Send the selected option and follow Telegram's real continuation
            # response (another option page or a comment request).
            from telethon.errors import FloodWaitError
            for attempt in range(2):
                try:
                    result = await client(ReportRequest(
                        peer=entity,
                        id=[message_id],
                        option=option,
                        message=comment or ''
                    ))
                    if isinstance(result, types.ReportResultReported):
                        return True
                    if isinstance(result, types.ReportResultAddComment):
                        await client(ReportRequest(
                            peer=entity,
                            id=[message_id],
                            option=result.option,
                            message=comment or 'Terms of service violation'
                        ))
                        return True
                    if isinstance(result, types.ReportResultChooseOption):
                        # Telegram requires a sub-option. Choose matching or first available real option!
                        chosen_opt = None
                        opts = getattr(result, "options", None) or []
                        if opts:
                            for opt in opts:
                                opt_text = (getattr(opt, "text", "") or "").lower()
                                if reason.lower() in opt_text or (reason == "child_abuse" and "child" in opt_text) or (reason in ("fake", "scam") and ("scam" in opt_text or "impersonat" in opt_text)) or (reason == "illegal_drugs" and ("drug" in opt_text or "good" in opt_text)) or (reason == "pornography" and ("porn" in opt_text or "adult" in opt_text)):
                                    chosen_opt = opt.option
                                    break
                            if not chosen_opt:
                                chosen_opt = opts[0].option
                        if chosen_opt:
                            sub_res = await client(ReportRequest(
                                peer=entity,
                                id=[message_id],
                                option=chosen_opt,
                                message=comment or ''
                            ))
                            if isinstance(sub_res, types.ReportResultAddComment):
                                await client(ReportRequest(
                                    peer=entity,
                                    id=[message_id],
                                    option=sub_res.option,
                                    message=comment or 'Terms of service violation'
                                ))
                            return True
                        return True
                    return True
                except FloodWaitError as fe:
                    log_activity(f"FloodWait on message report: waiting {fe.seconds}s")
                    if fe.seconds > 10:
                        return False
                    await asyncio.sleep(fe.seconds)
                except Exception as e:
                    # If option was rejected, query Telegram for live options and pick matching
                    try:
                        fresh = await client(ReportRequest(
                            peer=entity,
                            id=[message_id],
                            option=b"",
                            message=""
                        ))
                        if isinstance(fresh, types.ReportResultChooseOption):
                            opts = getattr(fresh, "options", []) or []
                            matched = None
                            for opt in opts:
                                o_txt = (getattr(opt, "text", "") or "").lower()
                                if reason.lower() in o_txt or (reason == "fake" and "scam" in o_txt) or (reason == "child_abuse" and "child" in o_txt) or (reason == "illegal_drugs" and ("drug" in o_txt or "good" in o_txt)) or (reason == "pornography" and ("adult" in o_txt or "porn" in o_txt)):
                                    matched = opt.option
                                    break
                            if not matched and opts:
                                matched = opts[0].option
                            if matched:
                                res2 = await client(ReportRequest(
                                    peer=entity,
                                    id=[message_id],
                                    option=matched,
                                    message=comment or ''
                                ))
                                if isinstance(res2, types.ReportResultAddComment):
                                    await client(ReportRequest(
                                        peer=entity,
                                        id=[message_id],
                                        option=res2.option,
                                        message=comment or 'Terms of service violation'
                                    ))
                                elif isinstance(res2, types.ReportResultChooseOption):
                                    sub_opts = getattr(res2, "options", []) or []
                                    if sub_opts:
                                        res3 = await client(ReportRequest(
                                            peer=entity,
                                            id=[message_id],
                                            option=sub_opts[0].option,
                                            message=comment or ''
                                        ))
                                        if isinstance(res3, types.ReportResultAddComment):
                                            await client(ReportRequest(
                                                peer=entity,
                                                id=[message_id],
                                                option=res3.option,
                                                message=comment or ''
                                            ))
                                return True
                    except Exception:
                        pass
                    # Only fallback to spam request if reason was spam
                    if reason == "spam":
                        try:
                            from telethon.tl.functions.channels import ReportSpamRequest as ChanReportSpam
                            await client(ChanReportSpam(channel=entity, participant=entity, id=[message_id]))
                            return True
                        except Exception:
                            pass
                    log_activity(f"Report execution error: {str(e)}")
                    return False
            return False

        except Exception as e:
            log_activity(f"Report error for {message_link}: {str(e)}")
            return False
            
    def _get_reason_obj(self, reason: str):
        if reason == "child_abuse":
            return types.InputReportReasonChildAbuse()
        elif reason == "violence":
            return types.InputReportReasonViolence()
        elif reason in ("illegal_drugs", "illegal_activities"):
            return types.InputReportReasonIllegalDrugs()
        elif reason == "pornography":
            return types.InputReportReasonPornography()
        elif reason == "fake" or reason == "scam":
            return types.InputReportReasonFake()
        elif reason == "personal_details":
            return types.InputReportReasonPersonalDetails()
        elif reason == "copyright":
            return types.InputReportReasonCopyright()
        elif reason == "geo_irrelevant":
            return types.InputReportReasonGeoIrrelevant()
        elif reason == "other":
            return types.InputReportReasonOther()
        return types.InputReportReasonSpam()

    async def report_profile_photo(
        self, client: TelegramClient, peer: any, photo: any, reason: str, comment: str = ""
    ) -> bool:
        """Report a profile photo or video of an entity"""
        try:
            input_photo = utils.get_input_photo(photo)
            entity = await client.get_input_entity(peer)
            reason_obj = self._get_reason_obj(reason)
            from telethon.errors import FloodWaitError
            for attempt in range(2):
                try:
                    await client(ReportProfilePhotoRequest(
                        peer=entity,
                        photo_id=input_photo,
                        reason=reason_obj,
                        message=comment or ''
                    ))
                    return True
                except FloodWaitError as fe:
                    log_activity(f"FloodWait on profile photo report: waiting {fe.seconds}s")
                    if fe.seconds > 10:
                        return False
                    await asyncio.sleep(fe.seconds)
                except Exception as e:
                    log_activity(f"Profile photo report execution error: {str(e)}")
                    return False
            return False
        except Exception as e:
            log_activity(f"Report profile photo error: {str(e)}")
            return False

    async def report_peer(
        self, client: TelegramClient, peer_id: str, reason: str, comment: str = ""
    ) -> bool:
        """Report a user, bot, channel, or group using the client"""
        try:
            try:
                entity = await self.resolve_entity(client, peer_id)
            except Exception as e:
                log_activity(f"Failed to get entity for peer {peer_id}: {str(e)}")
                return False
            
            reason_obj = self._get_reason_obj(reason)
            
            from telethon.errors import FloodWaitError
            for attempt in range(2):
                try:
                    input_peer = await client.get_input_entity(entity)
                    await client(ReportPeerRequest(
                        peer=input_peer,
                        reason=reason_obj,
                        message=comment or ''
                    ))
                    return True
                except FloodWaitError as fe:
                    log_activity(f"FloodWait on peer report: waiting {fe.seconds}s")
                    if fe.seconds > 10:
                        return False
                    await asyncio.sleep(fe.seconds)
                except Exception as e:
                    log_activity(f"Peer report execution error: {str(e)}")
                    return False
            return False
        except Exception as e:
            log_activity(f"Report peer error for {peer_id}: {str(e)}")
            return False

    async def resolve_entity(self, client: TelegramClient, peer):
        """Safely resolve an entity whether given by username, numeric User ID, link, or object."""
        if hasattr(peer, 'id') and not isinstance(peer, (str, int)):
            return peer
        raw = str(peer).strip()
        s = raw
        if "://" in s:
            s = s.split("://", 1)[1]
        if s.lower().startswith(("t.me/", "telegram.me/")):
            s = s.split("/", 1)[1]
        s = s.split("?")[0].split("#")[0].strip("/")
        if s.startswith("c/"):
            sub = s.split("c/", 1)[1].strip("/").split("/")[0]
            if sub.isdigit():
                s = f"-100{sub}"
        elif "/" in s and not s.startswith("+") and not s.startswith("joinchat/"):
            s = s.split("/")[0]
        if s.startswith("@"):
            s = s[1:]
        try:
            if s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                return await client.get_entity(int(s))
            return await client.get_entity(s)
        except Exception:
            try:
                if s.isdigit():
                    from telethon.tl.types import PeerUser
                    return await client.get_entity(PeerUser(int(s)))
            except Exception:
                pass
            try:
                return await client.get_entity(raw)
            except Exception:
                pass
            return await client.get_entity(peer)

    async def report_profile_photo(
        self, client: TelegramClient, peer_id, photo_or_id=None, reason: str = "spam", comment: str = ""
    ) -> bool:
        """Report profile photo of a user, group, or channel.
        
        Supports passing photo object / ID explicitly, or falls back to legacy (peer_id, reason, comment).
        """
        # Handle legacy 4-arg signature where photo_or_id is actually reason string
        if isinstance(photo_or_id, str) and not comment and reason == "spam":
            comment = reason
            reason = photo_or_id
            photo_or_id = None

        try:
            try:
                entity = await self.resolve_entity(client, peer_id)
            except Exception as e:
                log_activity(f"Failed to get entity for photo report {peer_id}: {str(e)}")
                return False

            from telethon.tl.functions.account import ReportProfilePhotoRequest
            from telethon.errors import FloodWaitError

            input_photo = None
            if hasattr(photo_or_id, 'id') and hasattr(photo_or_id, 'access_hash'):
                if isinstance(photo_or_id, types.InputPhoto):
                    input_photo = photo_or_id
                else:
                    input_photo = types.InputPhoto(
                        id=photo_or_id.id,
                        access_hash=photo_or_id.access_hash,
                        file_reference=getattr(photo_or_id, 'file_reference', b'') or b'',
                    )
            else:
                photos = await client.get_profile_photos(entity, limit=10)
                if not photos:
                    log_activity(f"No profile photo found for {peer_id}; falling back to peer report")
                    return await self.report_peer(client, peer_id, reason, comment)

                if isinstance(photo_or_id, int) and 1 <= photo_or_id <= len(photos):
                    photo = photos[photo_or_id - 1]
                else:
                    photo = photos[0]

                input_photo = types.InputPhoto(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=getattr(photo, 'file_reference', b'') or b'',
                )

            reason_obj = types.InputReportReasonSpam()
            if reason == "child_abuse":
                reason_obj = types.InputReportReasonChildAbuse()
            elif reason == "violence":
                reason_obj = types.InputReportReasonViolence()
            elif reason in ("illegal_drugs", "illegal_activities"):
                reason_obj = types.InputReportReasonIllegalDrugs()
            elif reason == "pornography":
                reason_obj = types.InputReportReasonPornography()
            elif reason in ("fake", "scam"):
                reason_obj = types.InputReportReasonFake()
            elif reason == "personal_details":
                reason_obj = types.InputReportReasonPersonalDetails()
            elif reason == "copyright":
                reason_obj = types.InputReportReasonCopyright()
            elif reason == "geo_irrelevant":
                reason_obj = types.InputReportReasonGeoIrrelevant()
            elif reason == "other":
                reason_obj = types.InputReportReasonOther()

            for attempt in range(2):
                try:
                    await client(ReportProfilePhotoRequest(
                        peer=entity,
                        photo_id=input_photo,
                        reason=reason_obj,
                        message=comment or ""
                    ))
                    return True
                except FloodWaitError as fe:
                    log_activity(f"FloodWait on profile photo report: waiting {fe.seconds}s")
                    if fe.seconds > 10:
                        return False
                    await asyncio.sleep(fe.seconds)
                except Exception as e:
                    log_activity(f"Profile photo report execution error: {str(e)}")
                    return await self.report_peer(client, peer_id, reason, comment)
            return False
        except Exception as e:
            log_activity(f"Report profile photo error for {peer_id}: {str(e)}")
            return False

    async def run_report_session(
        self, target: str, message_links: list, reports_per_account: int,
        delay: int, reason: str, comment: str, progress_message,
        allowed_phones=None, reason_option: str = "", reason_choices: list = None,
        dist_mode: str = "round_robin", account_rules: dict = None,
        target_type: str = "channel_msgs"
    ):
        """Run complete report session sequentially."""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No joined/active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}
        
        total_accounts = len(active_accounts)
        items_list = list(message_links) if message_links else [None]

        # Precompute exact execution spec for each account
        account_execution_specs = []
        total_reports = 0

        for a_idx, a_acc in enumerate(active_accounts):
            a_phone = a_acc[1]
            a_rule = (account_rules or {}).get(a_phone) or {}

            # 1. Assigned message items
            if a_rule.get("messages") is not None and a_rule.get("messages") != "all" and message_links:
                r_indices = a_rule.get("messages")
                if isinstance(r_indices, list):
                    a_items = [message_links[i] for i in r_indices if 0 <= i < len(message_links)]
                else:
                    a_items = items_list
                if not a_items:
                    a_items = items_list
            elif dist_mode == "round_robin" and message_links:
                num_msgs = len(message_links)
                if total_accounts <= num_msgs:
                    a_items = [m for i, m in enumerate(message_links) if i % total_accounts == a_idx]
                else:
                    a_items = [message_links[a_idx % num_msgs]]
            else:
                a_items = items_list

            # 2. Assigned reasons
            if a_rule.get("reasons"):
                a_choices = list(a_rule.get("reasons"))
            elif dist_mode in ("auto_distribute", "split", "round_robin") and reason_choices:
                a_choices = [reason_choices[a_idx % len(reason_choices)]]
            elif reason_choices:
                a_choices = list(reason_choices)
            else:
                a_choices = [{
                    "reason": reason or "spam",
                    "detail": (reason or "spam").capitalize(),
                    "option": reason_option or "",
                }]

            # 3. Iterations per message: in Full Mass ('all') ensure every selected reason executes
            if len(a_choices) > 1 and dist_mode not in ("auto_distribute", "split", "round_robin"):
                a_reps = max(reports_per_account, len(a_choices))
            else:
                a_reps = max(1, reports_per_account)

            account_execution_specs.append((a_items, a_choices, a_reps, a_rule))
            total_reports += len(a_items) * a_reps

        completed_reports = 0
        success_count = 0
        failed_count = 0
        
        start_time = datetime.now()
        
        for acc_idx, account in enumerate(active_accounts):
            if self.should_stop:
                await progress_message.edit("Report session stopped by user!")
                break

            account_id, phone, session_file, status, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account
            client = None

            acc_items, choices, effective_reps, a_rule = account_execution_specs[acc_idx]

            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    log_activity(f"Session file missing for {mask_phone(phone)}; skipping")
                    continue
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                # Set account status to ONLINE
                await set_account_online(client, offline=False)

                if target_type in ("account_profile", "account_pfp", "bot_profile", "bot_pfp"):
                    log_activity(f"Account {phone} targeting user/bot profile {target} (no join needed)")
                else:
                    joined, join_message = await ensure_account_joined(client, target)
                    if not joined:
                        log_activity(f"Account {phone} not joined to {target}; skipping: {join_message}")
                        continue
                    log_activity(f"Account {phone} joined {target} ({join_message})")

                for msg_idx, message_link in enumerate(acc_items):
                    if self.should_stop:
                        break

                    for report_num in range(effective_reps):
                        if self.should_stop:
                            break

                        await set_account_online(client, offline=False)

                        choice = choices[report_num % len(choices)]
                        report_reason = choice.get("reason") or reason or "spam"
                        report_option = choice.get("option") or reason_option or ""
                        report_label = choice.get("detail") or report_reason

                        choice_comment = (choice.get("comment") or "").strip()
                        if choice_comment:
                            report_comment = choice_comment
                        else:
                            active_comment = a_rule.get("comment") if (a_rule.get("comment") is not None and a_rule.get("comment") != "") else comment
                            if active_comment:
                                c_lines = [l.strip() for l in active_comment.split("\n") if l.strip()]
                                if len(c_lines) > 1:
                                    report_comment = c_lines[report_num % len(c_lines)]
                                else:
                                    report_comment = active_comment
                            else:
                                report_comment = ""

                        if "pfp" in target_type:
                            success = await self.report_profile_photo(
                                client, target, None, report_reason, report_comment
                            )
                        elif "peer" in target_type or "profile" in target_type or not message_link:
                            success = await self.report_peer(
                                client, target, report_reason, report_comment
                            )
                        else:
                            success = await self.report_message(
                                client, target, message_link, report_reason, report_comment,
                                report_option
                            )

                        log_item = message_link or target
                        if success:
                            success_count += 1
                            db.add_report_log(phone, target, log_item, "success", report_label)
                        else:
                            failed_count += 1
                            db.add_report_log(phone, target, log_item, "failed", report_label)

                        completed_reports += 1

                        percentage = (completed_reports / total_reports) * 100 if total_reports > 0 else 100
                        progress_bar = create_progress_bar(percentage)

                        elapsed = datetime.now() - start_time
                        eta = (elapsed / completed_reports) * (total_reports - completed_reports) if completed_reports > 0 else timedelta()

                        status_text = (
                            f"**Reporting Progress (Sequential)**\n\n"
                            f"Account: {acc_idx + 1}/{total_accounts}\n"
                            f"Phone: {mask_phone(phone)}\n"
                            f"Message: {msg_idx + 1}/{len(acc_items)}\n"
                            f"Reports: {report_num + 1}/{effective_reps}\n"
                            f"Reason: {report_label}\n\n"
                            f"{progress_bar}\n\n"
                            f"Success: {success_count}\n"
                            f"Failed: {failed_count}\n"
                            f"Total: {completed_reports}/{total_reports}\n\n"
                            f"Elapsed: {str(elapsed).split('.')[0]}\n"
                            f"ETA: {str(eta).split('.')[0]}"
                        )

                        try:
                            await progress_message.edit(status_text)
                        except:
                            pass

                        if not self.should_stop and (completed_reports < total_reports):
                            await asyncio.sleep(delay)

                db.update_account_status(phone, "active")

            except Exception as e:
                log_activity(f"Account {phone} error: {str(e)}")
                db.update_account_status(phone, "error", True)
            finally:
                if client is not None:
                    try:
                        await set_account_online(client, offline=True)
                        await client.disconnect()
                    except Exception:
                        pass
        
        db.add_report_stats(total_reports, success_count, failed_count)
        
        final_text = (
            f"**Report Session Complete**\n\n"
            f"Total Reports: {completed_reports}/{total_reports}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed_count}\n"
            f"Duration: {str(datetime.now() - start_time).split('.')[0]}\n"
            f"Accounts Used: {total_accounts}\n\n"
            f"Target: {target}\n"
        )
        
        try:
            await progress_message.edit(final_text)
        except:
            pass
        return {
            "success": success_count,
            "failed": failed_count,
            "completed": completed_reports,
            "total": total_reports,
        }

    async def run_parallel_report_session(
        self, target: str, message_links: list, reports_per_account: int,
        reason: str, comment: str, progress_message,
        allowed_phones=None, reason_option: str = "", reason_choices: list = None,
        dist_mode: str = "round_robin", account_rules: dict = None,
        target_type: str = "channel_msgs"
    ):
        """Run complete report session concurrently (Turbo Mode)."""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No joined/active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}
        
        total_accounts = len(active_accounts)
        items_list = list(message_links) if message_links else [None]

        # Precompute exact execution spec for each account
        account_execution_specs = {}
        total_reports = 0

        for a_idx, a_acc in enumerate(active_accounts):
            a_phone = a_acc[1]
            a_rule = (account_rules or {}).get(a_phone) or {}

            if a_rule.get("messages") is not None and a_rule.get("messages") != "all" and message_links:
                r_indices = a_rule.get("messages")
                if isinstance(r_indices, list):
                    a_items = [message_links[i] for i in r_indices if 0 <= i < len(message_links)]
                else:
                    a_items = items_list
                if not a_items:
                    a_items = items_list
            elif dist_mode == "round_robin" and message_links:
                num_msgs = len(message_links)
                if total_accounts <= num_msgs:
                    a_items = [m for i, m in enumerate(message_links) if i % total_accounts == a_idx]
                else:
                    a_items = [message_links[a_idx % num_msgs]]
            else:
                a_items = items_list

            if a_rule.get("reasons"):
                a_choices = list(a_rule.get("reasons"))
            elif dist_mode in ("auto_distribute", "split", "round_robin") and reason_choices:
                a_choices = [reason_choices[a_idx % len(reason_choices)]]
            elif reason_choices:
                a_choices = list(reason_choices)
            else:
                a_choices = [{
                    "reason": reason or "spam",
                    "detail": (reason or "spam").capitalize(),
                    "option": reason_option or "",
                }]

            if len(a_choices) > 1 and dist_mode not in ("auto_distribute", "split", "round_robin"):
                a_reps = max(reports_per_account, len(a_choices))
            else:
                a_reps = max(1, reports_per_account)

            account_execution_specs[a_phone] = (a_items, a_choices, a_reps, a_rule)
            total_reports += len(a_items) * a_reps

        completed_reports = 0
        success_count = 0
        failed_count = 0
        
        start_time = datetime.now()
        progress_lock = asyncio.Lock()
        
        async def update_progress_ui(phone: str, acc_num: int, reason_lbl: str = None):
            async with progress_lock:
                percentage = (completed_reports / total_reports) * 100 if total_reports > 0 else 100
                progress_bar = create_progress_bar(percentage)
                
                elapsed = datetime.now() - start_time
                eta = (elapsed / completed_reports) * (total_reports - completed_reports) if completed_reports > 0 else timedelta()
                
                status_text = (
                    f"**Turbo Mode Active (Parallel Processing)**\n\n"
                    f"Running {total_accounts} accounts in parallel...\n"
                    f"Active Worker: {mask_phone(phone)} (#{acc_num})\n"
                    f"Target: {target}\n"
                    f"Reason: {reason_lbl or reason}\n\n"
                    f"{progress_bar}\n\n"
                    f"Success: {success_count}\n"
                    f"Failed: {failed_count}\n"
                    f"Total: {completed_reports}/{total_reports}\n\n"
                    f"Elapsed: {str(elapsed).split('.')[0]}\n"
                    f"ETA: {str(eta).split('.')[0]}"
                )
                try:
                    await progress_message.edit(status_text)
                except:
                    pass

        async def account_worker(account_idx: int, account_data: tuple):
            nonlocal completed_reports, success_count, failed_count
            _, phone, session_file, _, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account_data

            if self.should_stop:
                return

            client = None
            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    log_activity(f"[Worker {mask_phone(phone)}] Session file missing; skipping")
                    return
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                # Set account status to ONLINE
                await set_account_online(client, offline=False)

                if target_type in ("account_profile", "account_pfp", "bot_profile", "bot_pfp"):
                    log_activity(f"[Worker {phone}] Targeting user/bot profile {target} (no join needed)")
                else:
                    joined, join_message = await ensure_account_joined(client, target)
                    if not joined:
                        log_activity(f"[Worker {phone}] Not joined to {target}; skipping: {join_message}")
                        return

                acc_items, choices, effective_reps, a_rule = account_execution_specs.get(
                    phone, (items_list, [{}], max(1, reports_per_account), {})
                )

                for msg_idx, message_link in enumerate(acc_items):
                    if self.should_stop:
                        break

                    for report_num in range(effective_reps):
                        if self.should_stop:
                            break

                        await set_account_online(client, offline=False)

                        choice = choices[report_num % len(choices)]
                        report_reason = choice.get("reason") or reason or "spam"
                        report_option = choice.get("option") or reason_option or ""
                        report_label = choice.get("detail") or report_reason

                        choice_comment = (choice.get("comment") or "").strip()
                        if choice_comment:
                            report_comment = choice_comment
                        else:
                            active_comment = a_rule.get("comment") if (a_rule.get("comment") is not None and a_rule.get("comment") != "") else comment
                            if active_comment:
                                c_lines = [l.strip() for l in active_comment.split("\n") if l.strip()]
                                if len(c_lines) > 1:
                                    report_comment = c_lines[report_num % len(c_lines)]
                                else:
                                    report_comment = active_comment
                            else:
                                report_comment = ""

                        if "pfp" in target_type:
                            success = await self.report_profile_photo(
                                client, target, None, report_reason, report_comment
                            )
                        elif "peer" in target_type or "profile" in target_type or not message_link:
                            success = await self.report_peer(
                                client, target, report_reason, report_comment
                            )
                        else:
                            success = await self.report_message(
                                client, target, message_link, report_reason, report_comment,
                                report_option
                            )

                        log_item = message_link or target
                        async with progress_lock:
                            if success:
                                success_count += 1
                                db.add_report_log(phone, target, log_item, "success", report_label)
                            else:
                                failed_count += 1
                                db.add_report_log(phone, target, log_item, "failed", report_label)

                            completed_reports += 1

                        await update_progress_ui(phone, account_idx + 1, report_label)
                        await asyncio.sleep(0.5)

                db.update_account_status(phone, "active")

            except Exception as e:
                log_activity(f"[Worker {phone}] Fatal error: {str(e)}")
                db.update_account_status(phone, "error", True)
            finally:
                if client is not None:
                    try:
                        await set_account_online(client, offline=True)
                        await client.disconnect()
                    except Exception:
                        pass
        
        tasks = []
        sem = asyncio.Semaphore(10)
        
        async def sem_worker(idx, account):
            async with sem:
                await account_worker(idx, account)
                
        for idx, account in enumerate(active_accounts):
            tasks.append(sem_worker(idx, account))
            
        await asyncio.gather(*tasks)
        
        db.add_report_stats(total_reports, success_count, failed_count)
        
        final_text = (
            f"**Turbo Report Session Complete**\n\n"
            f"Mode: Turbo (Parallel)\n"
            f"Total Reports: {completed_reports}/{total_reports}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed_count}\n"
            f"Duration: {str(datetime.now() - start_time).split('.')[0]}\n"
            f"Accounts Used: {total_accounts}\n\n"
            f"Target: {target}\n"
        )
        try:
            await progress_message.edit(final_text)
        except:
            pass
        return {
            "success": success_count,
            "failed": failed_count,
            "completed": completed_reports,
            "total": total_reports,
        }

    async def run_peer_report_session(
        self, peer_id: str, reason: str, reports_per_account: int, comment: str, progress_message,
        allowed_phones=None
    ):
        """Run complete report session for a user/bot sequentially"""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No joined/active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}
        
        total_accounts = len(active_accounts)
        total_reports = total_accounts * reports_per_account
        completed_reports = 0
        success_count = 0
        failed_count = 0
        
        start_time = datetime.now()
        
        for acc_idx, account in enumerate(active_accounts):
            if self.should_stop:
                await progress_message.edit("Report session stopped by user!")
                break
            
            account_id, phone, session_file, status, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account
            
            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    log_activity(f"Session file missing for {mask_phone(phone)}; skipping")
                    continue
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                await set_account_online(client, offline=False)
                
                for report_num in range(reports_per_account):
                    if self.should_stop:
                        break
                        
                    success = await self.report_peer(client, peer_id, reason, comment)
                    
                    if success:
                        success_count += 1
                    else:
                        failed_count += 1
                    
                    completed_reports += 1
                    
                    percentage = (completed_reports / total_reports) * 100
                    progress_bar = create_progress_bar(percentage)
                    
                    status_text = (
                        f"**User/Bot Reporting Progress (Sequential)**\n\n"
                        f"Account: {acc_idx + 1}/{total_accounts}\n"
                        f"Phone: {mask_phone(phone)}\n"
                        f"Reports: {report_num + 1}/{reports_per_account}\n\n"
                        f"{progress_bar}\n\n"
                        f"Success: {success_count}\n"
                        f"Failed: {failed_count}\n"
                        f"Total: {completed_reports}/{total_reports}"
                    )
                    
                    try:
                        await progress_message.edit(status_text)
                    except:
                        pass
                    
                    await asyncio.sleep(1)
                
                await set_account_online(client, offline=True)
                await client.disconnect()
                await asyncio.sleep(1)
                
            except Exception as e:
                log_activity(f"Account {phone} error: {str(e)}")
                continue
        
        final_text = (
            f"**User/Bot Report Complete**\n\n"
            f"Total Reports: {completed_reports}/{total_reports}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed_count}\n"
            f"Duration: {str(datetime.now() - start_time).split('.')[0]}\n"
            f"Accounts Used: {total_accounts}\n\n"
            f"Target: {peer_id}\n"
        )
        
        try:
            await progress_message.edit(final_text)
        except:
            pass

    async def run_parallel_peer_report_session(
        self, peer_id: str, reason: str, reports_per_account: int, comment: str, progress_message,
        allowed_phones=None
    ):
        """Run complete parallel peer report session (Turbo Mode)"""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No joined/active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}
        
        total_accounts = len(active_accounts)
        total_reports = total_accounts * reports_per_account
        completed_reports = 0
        success_count = 0
        failed_count = 0
        start_time = datetime.now()
        
        progress_lock = asyncio.Lock()
        
        async def update_progress_ui(phone: str, acc_num: int):
            async with progress_lock:
                percentage = (completed_reports / total_reports) * 100
                progress_bar = create_progress_bar(percentage)
                
                status_text = (
                    f"**Turbo Peer Report Active (Parallel)**\n\n"
                    f"Running {total_accounts} accounts in parallel...\n"
                    f"Active Worker: {mask_phone(phone)} (# {acc_num})\n"
                    f"Target: {peer_id}\n"
                    f"Reason: {reason}\n\n"
                    f"{progress_bar}\n\n"
                    f"Success: {success_count}\n"
                    f"Failed: {failed_count}\n"
                    f"Total: {completed_reports}/{total_reports}"
                )
                try:
                    await progress_message.edit(status_text)
                except:
                    pass

        async def peer_worker(account_idx: int, account_data: tuple):
            nonlocal completed_reports, success_count, failed_count
            _, phone, session_file, _, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account_data
            
            if self.should_stop:
                return
                
            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    log_activity(f"[PeerWorker {mask_phone(phone)}] Session file missing; skipping")
                    return
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                await set_account_online(client, offline=False)
                
                for report_num in range(reports_per_account):
                    if self.should_stop:
                        break
                        
                    success = await self.report_peer(client, peer_id, reason, comment)
                    
                    async with progress_lock:
                        if success:
                            success_count += 1
                        else:
                            failed_count += 1
                        completed_reports += 1
                        
                    await update_progress_ui(phone, account_idx + 1)
                    await asyncio.sleep(0.5)
                    
                await set_account_online(client, offline=True)
                await client.disconnect()
            except Exception as e:
                log_activity(f"[Peer Worker {phone}] Error: {str(e)}")
                
        tasks = []
        sem = asyncio.Semaphore(10)
        async def sem_worker(idx, account):
            async with sem:
                await peer_worker(idx, account)
                
        for idx, account in enumerate(active_accounts):
            tasks.append(sem_worker(idx, account))
            
        await asyncio.gather(*tasks)
        
        final_text = (
            f"**Turbo Peer Report Complete**\n\n"
            f"Mode: Turbo (Parallel)\n"
            f"Total Reports: {completed_reports}/{total_reports}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed_count}\n"
            f"Duration: {str(datetime.now() - start_time).split('.')[0]}\n"
            f"Accounts Used: {total_accounts}\n\n"
            f"Target: {peer_id}\n"
        )
        try:
            await progress_message.edit(final_text)
        except:
            pass

    async def run_profile_photo_report_session(
        self, target: str, pfp_mode: str, pfp_indices: list, reports_per_account: int,
        delay: int, reason: str, comment: str, progress_message, allowed_phones=None
    ):
        """Run profile photo/video reporting session sequentially."""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}

        completed_reports = 0
        success_count = 0
        failed_count = 0
        total_accounts = len(active_accounts)

        num_photos_est = 1 if pfp_mode == 'single' else (len(pfp_indices) if isinstance(pfp_indices, list) else 1)
        total_reports = total_accounts * reports_per_account * max(1, num_photos_est)
        start_time = datetime.now()

        for acc_idx, account in enumerate(active_accounts):
            if self.should_stop:
                await progress_message.edit("Report session stopped by user!")
                break

            account_id, phone, session_file, status, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account
            client = None
            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    continue
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                await set_account_online(client, offline=False)

                try:
                    kind, _ = parse_target_identifier(target)
                    if kind in ("invite", "username", "id"):
                        await ensure_account_joined(client, target)
                except Exception:
                    pass

                try:
                    entity = await self.resolve_entity(client, target)
                except Exception as e:
                    log_activity(f"Failed to get entity {target} for {phone}: {e}")
                    continue

                photos = await client.get_profile_photos(entity)
                if not photos:
                    log_activity(f"No profile photos found for {target} with {phone}")
                    continue

                if pfp_mode == 'single':
                    target_photos = [photos[0]]
                elif pfp_mode == 'all':
                    target_photos = list(photos)
                elif isinstance(pfp_indices, list):
                    target_photos = []
                    for idx in pfp_indices:
                        if 1 <= idx <= len(photos):
                            target_photos.append(photos[idx - 1])
                    if not target_photos:
                        target_photos = [photos[0]]
                else:
                    target_photos = [photos[0]]

                for p_idx, photo in enumerate(target_photos):
                    for rep_i in range(reports_per_account):
                        if self.should_stop:
                            break
                        res = await self.report_profile_photo(client, entity, photo, reason, comment)
                        if res:
                            success_count += 1
                        else:
                            failed_count += 1
                        completed_reports += 1

                        pct = (completed_reports / max(1, total_reports)) * 100
                        pbar = create_progress_bar(pct)
                        try:
                            await progress_message.edit(
                                f"**Profile Media Reporting in Progress...**\n\n"
                                f"Target: `{target}`\n"
                                f"Worker: `{phone}` (#{acc_idx + 1}/{total_accounts})\n"
                                f"Photo: #{p_idx + 1}/{len(target_photos)} (Report {rep_i + 1}/{reports_per_account})\n"
                                f"Progress: {pbar} ({completed_reports}/{total_reports})\n"
                                f"Success: {success_count} | Failed: {failed_count}"
                            )
                        except Exception:
                            pass

                        if delay > 0:
                            await asyncio.sleep(delay)

                await set_account_online(client, offline=True)
                await client.disconnect()
            except Exception as e:
                log_activity(f"Error in profile photo reporting for {phone}: {e}")
            finally:
                if client and client.is_connected():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

        db.add_report_stats(total_reports, success_count, failed_count)
        return {"success": success_count, "failed": failed_count, "completed": completed_reports, "total": total_reports}

    async def run_parallel_profile_photo_report_session(
        self, target: str, pfp_mode: str, pfp_indices: list, reports_per_account: int,
        reason: str, comment: str, progress_message, allowed_phones=None
    ):
        """Run profile photo/video reporting session in parallel (Turbo mode)."""
        active_accounts = db.get_active_accounts()
        if allowed_phones is not None:
            allowed_phones = set(allowed_phones)
            active_accounts = [acc for acc in active_accounts if acc[1] in allowed_phones]
        if not active_accounts:
            await progress_message.edit("No active accounts available.")
            return {"success": 0, "failed": 0, "completed": 0, "total": 0}

        completed_reports = 0
        success_count = 0
        failed_count = 0
        total_accounts = len(active_accounts)
        progress_lock = asyncio.Lock()

        num_photos_est = 1 if pfp_mode == 'single' else (len(pfp_indices) if isinstance(pfp_indices, list) else 1)
        total_reports = total_accounts * reports_per_account * max(1, num_photos_est)
        start_time = datetime.now()

        async def update_progress_ui(phone, acc_num, photo_num, total_photos):
            pct = (completed_reports / max(1, total_reports)) * 100
            pbar = create_progress_bar(pct)
            try:
                await progress_message.edit(
                    f"**Turbo Profile Media Reporting...**\n\n"
                    f"Target: `{target}`\n"
                    f"Active Worker: `{phone}` (#{acc_num})\n"
                    f"Photo: #{photo_num}/{total_photos}\n"
                    f"Progress: {pbar} ({completed_reports}/{total_reports})\n"
                    f"Success: {success_count} | Failed: {failed_count}\n"
                    f"Mode: Turbo (Parallel)"
                )
            except Exception:
                pass

        async def photo_worker(account_idx, account):
            nonlocal completed_reports, success_count, failed_count
            if self.should_stop:
                return

            account_id, phone, session_file, status, proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass = account
            client = None
            try:
                session_path = resolve_session_path(session_file, phone)
                if not session_path:
                    return
                proxy_tuple = get_telethon_proxy(proxy_type, proxy_ip, proxy_port, proxy_user, proxy_pass)
                base_session = session_path[:-8] if session_path.lower().endswith('.session') else session_path
                client = TelegramClient(base_session, API_ID, API_HASH, proxy=proxy_tuple)
                await client.connect()
                await set_account_online(client, offline=False)

                try:
                    kind, _ = parse_target_identifier(target)
                    if kind in ("invite", "username", "id"):
                        await ensure_account_joined(client, target)
                except Exception:
                    pass

                try:
                    entity = await self.resolve_entity(client, target)
                except Exception as e:
                    log_activity(f"Failed to get entity {target} for {phone}: {e}")
                    return

                photos = await client.get_profile_photos(entity)
                if not photos:
                    return

                if pfp_mode == 'single':
                    target_photos = [photos[0]]
                elif pfp_mode == 'all':
                    target_photos = list(photos)
                elif isinstance(pfp_indices, list):
                    target_photos = []
                    for idx in pfp_indices:
                        if 1 <= idx <= len(photos):
                            target_photos.append(photos[idx - 1])
                    if not target_photos:
                        target_photos = [photos[0]]
                else:
                    target_photos = [photos[0]]

                for p_idx, photo in enumerate(target_photos):
                    for _ in range(reports_per_account):
                        if self.should_stop:
                            break
                        res = await self.report_profile_photo(client, entity, photo, reason, comment)
                        async with progress_lock:
                            if res:
                                success_count += 1
                            else:
                                failed_count += 1
                            completed_reports += 1
                        await update_progress_ui(phone, account_idx + 1, p_idx + 1, len(target_photos))
                        await asyncio.sleep(0.5)

                await set_account_online(client, offline=True)
                await client.disconnect()
            except Exception as e:
                log_activity(f"[Pfp Worker {phone}] Error: {str(e)}")
            finally:
                if client and client.is_connected():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

        tasks = []
        sem = asyncio.Semaphore(10)
        async def sem_worker(idx, account):
            async with sem:
                await photo_worker(idx, account)

        for idx, account in enumerate(active_accounts):
            tasks.append(sem_worker(idx, account))

        await asyncio.gather(*tasks)

        final_text = (
            f"**Turbo Profile Media Report Complete**\n\n"
            f"Mode: Turbo (Parallel)\n"
            f"Total Reports: {completed_reports}/{total_reports}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed_count}\n"
            f"Duration: {str(datetime.now() - start_time).split('.')[0]}\n"
            f"Accounts Used: {total_accounts}\n\n"
            f"Target: {target}\n"
        )
        try:
            await progress_message.edit(final_text)
        except Exception:
            pass
        db.add_report_stats(total_reports, success_count, failed_count)
        return {"success": success_count, "failed": failed_count, "completed": completed_reports, "total": total_reports}
