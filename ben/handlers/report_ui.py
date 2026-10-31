"""Reusable inline keyboards and Telegram report-reason metadata.

Telegram exposes report reasons tailored to the target type (Profile Photo, User,
Bot, Group, Channel, Message). Their display names below are the official names used
by Telegram clients.
"""

from telethon import Button
from config import MAX_REPORTS_PER_ACCOUNT, MIN_DELAY, MAX_DELAY


# 1. Profile Photo / Video (PFP) Report Reasons (Official Telegram App Choices)
PFP_REASON_CHOICES = [
    ("pornography", "🔞 Pornography", "Adult content, nudity, or sexually explicit photo/video."),
    ("child_abuse", "⚠️ Child Abuse", "Child sexual abuse material or exploitation."),
    ("violence", "🩸 Violence", "Graphic violence, gore, threats, or weapons."),
    ("personal_details", "🪪 Personal Details", "Non-consensual photo, private photos, or doxxing."),
    ("copyright", "©️ Copyright", "Unauthorized use of copyrighted or stolen picture."),
    ("other", "📝 Other", "Any other violation. Add details in the comment."),
]

# 2. User Account Report Reasons (Official Telegram App Choices)
USER_REASON_CHOICES = [
    ("fake", "🎭 Fake Account", "Impersonation, scams, identity theft, or deceptive profile."),
    ("spam", "📢 Spam", "Mass messaging, unwanted advertising, or automated spam."),
    ("violence", "🩸 Violence", "Threats of violence, encouragement of harm, or violent extremism."),
    ("child_abuse", "⚠️ Child Abuse", "Child exploitation, abuse, or illegal material involving minors."),
    ("pornography", "🔞 Pornography", "Sharing adult content or non-consensual sexual material."),
    ("illegal_drugs", "💊 Illegal Drugs", "Selling, buying, or promoting illegal drugs or substances."),
    ("personal_details", "🪪 Personal Details", "Doxxing, leaking phone numbers, IDs, or private data."),
    ("other", "📝 Other", "Other rule violations. Provide explanation in the comment."),
]

# 3. Bot Report Reasons (Official Telegram App Choices)
BOT_REASON_CHOICES = [
    ("fake", "🎭 Fake / Scam", "Impersonating official entities, phishing, or scamming users."),
    ("spam", "📢 Spam", "Spam broadcasts, unsolicited adverts, or flooding chats."),
    ("violence", "🩸 Violence", "Threats, violence, or dangerous content."),
    ("child_abuse", "⚠️ Child Abuse", "Child safety violation or illegal material."),
    ("pornography", "🔞 Pornography", "Adult or sexually explicit content."),
    ("illegal_drugs", "💊 Illegal Drugs", "Selling drugs, chemical substances, or contraband."),
    ("personal_details", "🪪 Personal Details", "Distributing private records, dox data, or passwords."),
    ("other", "📝 Other", "Any other abuse. Explain in the comment."),
]

# 4. Group (Chat) Report Reasons (Official Telegram App Choices)
GROUP_REASON_CHOICES = [
    ("spam", "📢 Spam", "Spam group, promotional flooding, or ad scams."),
    ("fake", "🎭 Fake / Scam", "Scam group, fake official group, or impersonation."),
    ("violence", "🩸 Violence", "Violent threats, physical harm, or extremism."),
    ("child_abuse", "⚠️ Child Abuse", "Child sexual abuse or child exploitation material."),
    ("pornography", "🔞 Pornography", "Unsolicited or non-consensual pornography."),
    ("illegal_drugs", "💊 Illegal Drugs", "Drug dealing, distribution, or prohibited goods."),
    ("personal_details", "🪪 Personal Details", "Doxxing, leaked personal data, or stalking."),
    ("other", "📝 Other", "Other violations. Please specify in comment."),
]

# 5. Channel Report Reasons (Official Telegram App Choices)
CHANNEL_REASON_CHOICES = [
    ("spam", "📢 Spam", "Spam channel, scam links, or malicious promotion."),
    ("fake", "🎭 Fake Channel", "Impersonating known brands, scams, or fraudulent channels."),
    ("violence", "🩸 Violence", "Graphic violence, warfare gore, or terrorist content."),
    ("child_abuse", "⚠️ Child Abuse", "Child safety violations or exploitation."),
    ("pornography", "🔞 Pornography", "Adult material where prohibited."),
    ("illegal_drugs", "💊 Illegal Drugs", "Selling or trafficking narcotics or controlled goods."),
    ("personal_details", "🪪 Personal Details", "Doxxing, leaking phone numbers, databases, or private info."),
    ("copyright", "©️ Copyright", "Pirated movies, music, courses, or copyright violations."),
    ("geo_irrelevant", "📍 Geo Irrelevant", "Irrelevant geographic or localized content."),
    ("other", "📝 Other", "Other violations. Add context in comment."),
]

# 6. Specific Message Report Reasons
MESSAGE_REASON_CHOICES = [
    ("spam", "📢 Spam", "Spam message or unsolicited advertising."),
    ("fake", "🎭 Fake / Scam", "Scam, phishing, or fraudulent message."),
    ("violence", "🩸 Violence", "Violent threat or incitement."),
    ("child_abuse", "⚠️ Child Abuse", "Child exploitation material."),
    ("pornography", "🔞 Pornography", "Adult sexual content."),
    ("illegal_drugs", "💊 Illegal Drugs", "Drug promotion or sales."),
    ("personal_details", "🪪 Personal Details", "Doxxing or private information."),
    ("copyright", "©️ Copyright", "Copyrighted media."),
    ("geo_irrelevant", "📍 Geo Irrelevant", "Geographically irrelevant content."),
    ("other", "📝 Other", "Other violation."),
]

# Master choices retained for backward compatibility
REPORT_REASON_CHOICES = [
    ("spam", "Spam", "Unsolicited advertising, mass messaging or automated spam."),
    ("fake", "Fake Account", "Scams, impersonation or a deceptive account."),
    ("violence", "Violence", "Threats, incitement or graphic violent content."),
    ("child_abuse", "Child Abuse", "Sexual content involving minors or child exploitation."),
    ("pornography", "Pornography", "Adult sexual content where it is not allowed."),
    ("illegal_drugs", "Illegal Drugs", "Drug sales, promotion or drug-related illegal activity."),
    ("personal_details", "Personal Details", "Doxxing, private information or non-consensual sharing."),
    ("copyright", "Copyright", "Unauthorized copyrighted material or stolen content."),
    ("geo_irrelevant", "Geo Irrelevant", "Content irrelevant to the channel's location or topic."),
    ("other", "Other", "Any other violation; add a clear explanation in the comment."),
]

REASON_LABELS = {key: label for key, label, _ in REPORT_REASON_CHOICES}
REASON_DESCRIPTIONS = {key: description for key, _, description in REPORT_REASON_CHOICES}
REASON_KEYS_BY_LABEL = {
    label.strip().lower(): key for key, label, _ in REPORT_REASON_CHOICES
}


def reason_key_from_label(label: str, fallback: str = "other") -> str:
    """Map an official Telegram reason label to its local reason key."""
    import re
    raw = (label or "").strip().lower()
    if raw in REASON_KEYS_BY_LABEL:
        return REASON_KEYS_BY_LABEL[raw]
    clean = re.sub(r'[^\w\s]', ' ', raw).strip()
    clean = re.sub(r'\s+', ' ', clean)
    if clean in REASON_KEYS_BY_LABEL:
        return REASON_KEYS_BY_LABEL[clean]
    aliases = {
        "fake account": "fake",
        "fake": "fake",
        "scam": "fake",
        "scam or fraud": "fake",
        "fake scam": "fake",
        "fake channel": "fake",
        "child abuse": "child_abuse",
        "violence": "violence",
        "illegal drugs": "illegal_drugs",
        "illegal goods and services": "illegal_drugs",
        "illegal adult content": "pornography",
        "adult content": "pornography",
        "pornography": "pornography",
        "personal details": "personal_details",
        "personal data": "personal_details",
        "copyright": "copyright",
        "spam": "spam",
        "geo irrelevant": "geo_irrelevant",
        "irrelevant geo": "geo_irrelevant",
        "irrelevant / geo": "geo_irrelevant",
        "it s not illegal but must be taken down": "geo_irrelevant",
        "it's not illegal, but must be taken down": "geo_irrelevant",
        "i don't like it": "other",
        "i don t like it": "other",
        "other": "other",
    }
    return aliases.get(clean, aliases.get(raw, fallback))


def reason_display(reason_key: str, detail: str = "") -> str:
    """Return the official reason label plus an optional real sub-option."""
    label = REASON_LABELS.get(reason_key)
    if not label:
        label = reason_key or "Other"
    detail = (detail or "").strip()
    return f"{label} - {detail}" if detail else label


def _preset_rows(values, prefix: str, per_row: int = 3):
    rows = []
    row = []
    for value in values:
        row.append(Button.inline(str(value), f"{prefix}:{value}".encode()))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def count_buttons(max_count: int = MAX_REPORTS_PER_ACCOUNT):
    presets = [1, 2, 3, 5, 10, 25, 50]
    values = [value for value in presets if value <= max_count]
    rows = _preset_rows(values, "count_pick")
    rows.append([Button.inline("Custom count", b"ui_custom_count")])
    return rows


def scrape_buttons():
    rows = _preset_rows([10, 20, 30, 50, 100], "scrape_pick")
    rows.append([Button.inline("Custom count", b"ui_custom_count")])
    return rows


def delay_buttons():
    presets = [1, 3, 5, 10, 30, 60]
    values = [value for value in presets if MIN_DELAY <= value <= MAX_DELAY]
    rows = _preset_rows(values, "delay_pick")
    rows.append([Button.inline("Custom delay", b"ui_custom_count")])
    return rows


def dist_mode_buttons():
    return [
        [btn_continue("1 Account per Message (Spread / Round-Robin)", b"dist_mode_rr")],
        [Button.inline("All Accounts to All Messages (Mass)", b"dist_mode_all")],
    ]


def exec_mode_buttons(peer: bool = False):
    prefix = b"peer_exec_mode_" if peer else b"exec_mode_"
    return [
        [Button.inline("Turbo Mode (Parallel)", prefix + b"turbo")],
        [Button.inline("Safe Mode (Sequential)", prefix + b"safe")],
    ]


def comment_buttons(peer: bool = False):
    prefix = b"peer_comment_" if peer else b"comment_"
    return [
        [Button.inline("Add comment", prefix + b"yes")],
        [Button.inline("No comment", prefix + b"no")],
    ]


def _build_choice_rows(choices, prefix: str, per_row: int = 2):
    rows = []
    row = []
    for item in choices:
        key, label = item[0], item[1]
        row.append(Button.inline(label, f"{prefix}_{key}".encode()))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def pfp_reason_buttons():
    """Reasons specific to Profile Photos/Videos."""
    return _build_choice_rows(PFP_REASON_CHOICES, "pfp_reason")


def user_reason_buttons():
    """Reasons specific to User Accounts."""
    return _build_choice_rows(USER_REASON_CHOICES, "peer_reason")


def bot_reason_buttons():
    """Reasons specific to Bots."""
    return _build_choice_rows(BOT_REASON_CHOICES, "peer_reason")


def group_reason_buttons():
    """Reasons specific to Groups."""
    return _build_choice_rows(GROUP_REASON_CHOICES, "peer_reason")


def channel_reason_buttons():
    """Reasons specific to Channels."""
    return _build_choice_rows(CHANNEL_REASON_CHOICES, "peer_reason")


def message_reason_buttons():
    """Reasons specific to Message links."""
    return _build_choice_rows(MESSAGE_REASON_CHOICES, "reason")


def peer_reason_buttons(target_type: str = None):
    """Main reason buttons for peer reports, specialized by target type."""
    if target_type == "pfp":
        return pfp_reason_buttons()
    elif target_type == "user":
        return user_reason_buttons()
    elif target_type == "bot":
        return bot_reason_buttons()
    elif target_type == "group":
        return group_reason_buttons()
    elif target_type == "channel":
        return channel_reason_buttons()
    return _build_choice_rows(REPORT_REASON_CHOICES, "peer_reason")


def report_option_buttons(options, prefix: str = "reason_opt", per_row: int = 2):
    """Buttons for real options returned by Telegram, indexed into saved state."""
    rows = []
    row = []
    for index, option in enumerate(options or []):
        label = str(option.get("text") or "Option")
        row.append(Button.inline(label, f"{prefix}:{index}".encode()))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def btn_continue(text: str = "Continue", data: bytes = b"continue") -> Button:
    return Button.inline(text, data, style="success")


def btn_back(text: str = "Back", data: bytes = b"back") -> Button:
    return Button.inline(text, data, style="danger")


def nav_buttons(back_data: bytes = None):
    rows = []
    if back_data:
        rows.append([btn_back("Back", back_data)])
    rows.append([Button.inline("Main Menu", b"menu:home")])
    return rows


def with_nav(rows, back_data: bytes = None):
    out = [list(r) for r in (rows or [])]
    for nr in nav_buttons(back_data):
        out.append(nr)
    return out
