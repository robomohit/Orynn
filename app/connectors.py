"""Connectors registry — shared between the dashboard (where you set them up)
and the agent (which uses linked connectors when running tasks).

Linked state is persisted in workspace/connectors.json. The widget never
configures connectors; it just consumes whatever's linked.

A "linked" connector means the dashboard has stored credentials or a flag that
unlocks the agent to use that surface. For OAuth services we don't actually
ship OAuth flow yet (free-tier scope), so "link" stores a minimal placeholder
that the agent treats as permission to drive the corresponding browser surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .state_store import read_json, workspace_state_path, write_json

# The full registry. `auth_kind` describes how a connector links:
#   browser  — agent drives the web UI; "link" just marks consent
#   token    — needs an API token stored in linked state
#   local    — no auth, available immediately (filesystem, clipboard, etc.)
CONNECTORS: list[dict] = [
    {"id": "gmail",    "label": "Gmail",         "icon": "mail",
     "tint": "#EA4335", "auth_kind": "browser",
     "tip": "Drive Gmail web in the browser to triage + draft",
     "task_template": (
         "Open https://mail.google.com. Scan the inbox (top 10 unread). "
         "For each: classify (reply-needed / FYI / trash) and draft a "
         "reply where appropriate. Don't send — save as drafts. "
         "Report a summary."),
     "default_mode": "computer_use"},
    {"id": "outlook",  "label": "Outlook",       "icon": "mail",
     "tint": "#0078D4", "auth_kind": "browser",
     "tip": "Drive Outlook web in the browser",
     "task_template": (
         "Open https://outlook.office.com. Triage top 10 unread, draft "
         "replies, save as drafts only."),
     "default_mode": "computer_use"},
    {"id": "gcal",     "label": "Google Calendar","icon": "calendar",
     "tint": "#4285F4", "auth_kind": "browser",
     "tip": "Read this week's schedule",
     "task_template": (
         "Open https://calendar.google.com and report my upcoming events "
         "for the next 7 days. Group by day. Flag any conflicts."),
     "default_mode": "computer_use"},
    {"id": "github",   "label": "GitHub",        "icon": "github",
     "tint": "#181717", "auth_kind": "browser",
     "tip": "Triage GitHub notifications + PRs",
     "task_template": (
         "Open https://github.com/notifications. List open PRs and issues "
         "assigned to me or awaiting my review. Group by repo."),
     "default_mode": "computer_use"},
    {"id": "slack",    "label": "Slack",         "icon": "slack",
     "tint": "#4A154B", "auth_kind": "browser",
     "tip": "Summarize Slack unreads",
     "task_template": (
         "Open https://app.slack.com. Visit each unread channel, summarize "
         "what was discussed (skip bot/notification channels). Don't post."),
     "default_mode": "computer_use"},
    {"id": "notion",   "label": "Notion",        "icon": "notion",
     "tint": "#000000", "auth_kind": "browser",
     "tip": "Search my Notion workspace",
     "task_template": (
         "Open https://www.notion.so. Search my workspace for the topic "
         "I specify next, summarize the top 3 hits. Topic: "),
     "default_mode": "computer_use"},
    {"id": "drive",    "label": "Google Drive",  "icon": "drive",
     "tint": "#0F9D58", "auth_kind": "browser",
     "tip": "Find a file in Drive",
     "task_template": (
         "Open https://drive.google.com and find the file I name next. "
         "Open it and summarize. File: "),
     "default_mode": "computer_use"},
    {"id": "youtube",  "label": "YouTube",       "icon": "youtube",
     "tint": "#FF0000", "auth_kind": "browser",
     "tip": "Summarize a YouTube video",
     "task_template": (
         "Open the YouTube URL below, fetch the transcript, produce a "
         "5-bullet summary with timestamps for key claims. URL: "),
     "default_mode": "computer_use"},
    {"id": "spotify",  "label": "Spotify",       "icon": "music",
     "tint": "#1DB954", "auth_kind": "browser",
     "tip": "Control playback + find music on Spotify web",
     "task_template": (
         "Open https://open.spotify.com. Do what I ask next — e.g. play a "
         "playlist, search an artist, or queue a song. Request: "),
     "default_mode": "computer_use"},
    {"id": "teams",    "label": "Microsoft Teams","icon": "video",
     "tint": "#6264A7", "auth_kind": "browser",
     "tip": "Triage Teams chats + channels",
     "task_template": (
         "Open https://teams.microsoft.com. Summarize unread chats and the "
         "channels I follow. Don't post anything."),
     "default_mode": "computer_use"},
    {"id": "whatsapp", "label": "WhatsApp",      "icon": "message",
     "tint": "#25D366", "auth_kind": "browser",
     "tip": "Read recent WhatsApp chats (WhatsApp Web)",
     "task_template": (
         "Open https://web.whatsapp.com. Summarize my unread conversations. "
         "Do NOT send any message unless I explicitly ask."),
     "default_mode": "computer_use"},
    {"id": "telegram", "label": "Telegram",      "icon": "message",
     "tint": "#26A5E4", "auth_kind": "browser",
     "tip": "Read recent Telegram chats (Telegram Web)",
     "task_template": (
         "Open https://web.telegram.org. Summarize my unread chats. Do NOT "
         "send anything unless I explicitly ask."),
     "default_mode": "computer_use"},
    {"id": "discord",  "label": "Discord",       "icon": "message",
     "tint": "#5865F2", "auth_kind": "browser",
     "tip": "Navigate Discord servers + channels",
     "task_template": (
         "Open the Discord app (or https://discord.com/app). Go to the server "
         "and channel I name, then summarize recent messages. Server/channel: "),
     "default_mode": "computer"},
    {"id": "linear",   "label": "Linear",        "icon": "ticket",
     "tint": "#5E6AD2", "auth_kind": "browser",
     "tip": "Review my Linear issues",
     "task_template": (
         "Open https://linear.app. List issues assigned to me, grouped by "
         "status. Flag anything overdue."),
     "default_mode": "computer_use"},
    {"id": "jira",     "label": "Jira",          "icon": "ticket",
     "tint": "#0052CC", "auth_kind": "browser",
     "tip": "Review my Jira tickets",
     "task_template": (
         "Open my Jira board. List tickets assigned to me, grouped by status. "
         "Summarize what's in progress."),
     "default_mode": "computer_use"},
    {"id": "trello",   "label": "Trello",        "icon": "ticket",
     "tint": "#0079BF", "auth_kind": "browser",
     "tip": "Review my Trello boards",
     "task_template": (
         "Open https://trello.com. Summarize the cards on the board I name, "
         "by list. Board: "),
     "default_mode": "computer_use"},
    # ── Windows desktop apps (driven by UIA — our fastest, screenshot-free path) ─
    {"id": "excel",    "label": "Excel",          "icon": "sheet",
     "tint": "#217346", "auth_kind": "app",
     "tip": "Drive Excel by control name (UIA) — read/edit cells, formulas, charts",
     "task_template": (
         "Open the Excel workbook I name and do what I ask — read a range, add a "
         "formula, sort, or build a chart. Confirm the result. Task: "),
     "default_mode": "computer"},
    {"id": "word",     "label": "Word",           "icon": "doc",
     "tint": "#2B579A", "auth_kind": "app",
     "tip": "Drive Word by control name (UIA) — draft, edit, format documents",
     "task_template": (
         "Open Word and do what I ask — draft a document, edit text, apply "
         "formatting. Save when done. Task: "),
     "default_mode": "computer"},
    {"id": "powerpoint","label": "PowerPoint",    "icon": "slides",
     "tint": "#B7472A", "auth_kind": "app",
     "tip": "Drive PowerPoint by control name (UIA) — build + edit slides",
     "task_template": (
         "Open PowerPoint and build/edit the deck I describe. Task: "),
     "default_mode": "computer"},
    {"id": "vscode",   "label": "VS Code",        "icon": "code",
     "tint": "#007ACC", "auth_kind": "app",
     "tip": "Open a project + run real coding tools (files, terminal, tests)",
     "task_template": (
         "Work in this project: read the relevant files, make the change I "
         "describe, run the tests, and report. Task: "),
     "default_mode": "coding"},
    # ── High-value web surfaces (agent drives the web UI) ───────────────────────
    {"id": "gdocs",    "label": "Google Docs",    "icon": "doc",
     "tint": "#4285F4", "auth_kind": "browser",
     "tip": "Draft + edit Google Docs in the browser",
     "task_template": (
         "Open https://docs.google.com. Create or open the doc I name and "
         "draft/edit as I ask. Don't share or delete. Task: "),
     "default_mode": "computer_use"},
    {"id": "gsheets",  "label": "Google Sheets",  "icon": "sheet",
     "tint": "#0F9D58", "auth_kind": "browser",
     "tip": "Read + edit Google Sheets in the browser",
     "task_template": (
         "Open https://sheets.google.com. In the sheet I name, read/enter data "
         "or add formulas as I ask. Confirm the values. Task: "),
     "default_mode": "computer_use"},
    {"id": "canva",    "label": "Canva",          "icon": "design",
     "tint": "#00C4CC", "auth_kind": "browser",
     "tip": "Search, create + export Canva designs",
     "task_template": (
         "Open https://www.canva.com. Find a template or design as I ask, edit "
         "the text/images, and export. Task: "),
     "default_mode": "computer_use"},
    {"id": "figma",    "label": "Figma",          "icon": "design",
     "tint": "#F24E1E", "auth_kind": "browser",
     "tip": "Read Figma frames + pull design context",
     "task_template": (
         "Open https://www.figma.com. Open the file I name, inspect the frames "
         "and report layout/styles (or generate code from them). Task: "),
     "default_mode": "computer_use"},
    {"id": "maps",     "label": "Google Maps",    "icon": "map",
     "tint": "#34A853", "auth_kind": "browser",
     "tip": "Look up places, directions + travel times",
     "task_template": (
         "Open https://maps.google.com. Look up the place or route I ask for and "
         "report directions, distance, and time. Task: "),
     "default_mode": "computer_use"},
    {"id": "amazon",   "label": "Amazon",         "icon": "cart",
     "tint": "#FF9900", "auth_kind": "browser",
     "tip": "Search products, compare + track orders (never buy without consent)",
     "task_template": (
         "Open https://www.amazon.com. Search for the product I name, compare the "
         "top options on price/rating, and report. Do NOT place an order. Task: "),
     "default_mode": "computer_use"},
    {"id": "linkedin", "label": "LinkedIn",       "icon": "briefcase",
     "tint": "#0A66C2", "auth_kind": "browser",
     "tip": "Read feed, profiles + jobs (never post/connect without consent)",
     "task_template": (
         "Open https://www.linkedin.com. Do the read-only task I ask — scan the "
         "feed, a profile, or job listings — and summarize. Don't post/connect. Task: "),
     "default_mode": "computer_use"},
    {"id": "twitter",  "label": "X (Twitter)",    "icon": "social",
     "tint": "#000000", "auth_kind": "browser",
     "tip": "Read timeline / search posts (never post without consent)",
     "task_template": (
         "Open https://x.com. Read the timeline or search the topic I name and "
         "summarize. Do NOT post, reply, or like unless I explicitly ask. Task: "),
     "default_mode": "computer_use"},
    {"id": "reddit",   "label": "Reddit",         "icon": "social",
     "tint": "#FF4500", "auth_kind": "browser",
     "tip": "Browse + summarize subreddits and threads",
     "task_template": (
         "Open https://www.reddit.com. Find the subreddit or thread I name and "
         "summarize the top posts/comments. Don't post or vote. Task: "),
     "default_mode": "computer_use"},
    {"id": "todoist",  "label": "Todoist",        "icon": "check",
     "tint": "#E44332", "auth_kind": "browser",
     "tip": "Review + add tasks in Todoist",
     "task_template": (
         "Open https://todoist.com/app. List my tasks due today/overdue, grouped "
         "by project. Add a task only if I explicitly ask. Task: "),
     "default_mode": "computer_use"},
    {"id": "calendly", "label": "Calendly",       "icon": "calendar",
     "tint": "#006BFF", "auth_kind": "browser",
     "tip": "Check event types + scheduled meetings",
     "task_template": (
         "Open https://calendly.com/app. Report my event types and upcoming "
         "scheduled meetings. Don't change availability unless asked. Task: "),
     "default_mode": "computer_use"},
    {"id": "hubspot",  "label": "HubSpot",        "icon": "crm",
     "tint": "#FF7A59", "auth_kind": "browser",
     "tip": "Look up CRM contacts, deals + pipeline",
     "task_template": (
         "Open https://app.hubspot.com. Look up the contact/deal I name or "
         "summarize the pipeline. Don't edit records unless asked. Task: "),
     "default_mode": "computer_use"},
    {"id": "filesystem","label": "Local Files",  "icon": "folder",
     "tint": "#4B5563", "auth_kind": "local",
     "tip": "Read / write local files (always available)",
     "task_template": "",
     "default_mode": "coding"},
    {"id": "clipboard","label": "Clipboard",     "icon": "clipboard",
     "tint": "#4B5563", "auth_kind": "local",
     "tip": "Read / write the system clipboard (always available)",
     "task_template": "",
     "default_mode": "auto"},
]


# ── Per-connector SKILLS (the "manual" for each tool) ────────────────────────
# Each entry: keywords that mark the connector relevant to a goal, and a `skill`
# manual the agent receives when that connector is linked AND relevant. The
# manual tells the agent exactly how to drive that surface well + the safety
# rails (never send/post/delete without the user's say-so).
CONNECTOR_SKILLS: dict[str, dict] = {
    "gmail": {"keywords": ["gmail", "email", "inbox", "mail"], "skill": (
        "GMAIL (web). Open https://mail.google.com. The user is already signed in.\n"
        "- Triage: read the subject + snippet of the top unread threads; classify each "
        "as reply-needed / FYI / promo.\n"
        "- To draft: open the thread, click Reply, write the draft, then Save (Ctrl+S) or "
        "close — it auto-saves to Drafts. NEVER click Send unless the user explicitly asked.\n"
        "- Search with the top search bar using Gmail operators (from:, is:unread, after:).\n"
        "- Report a concise summary; do not delete or archive without being asked.")},
    "outlook": {"keywords": ["outlook", "office", "email"], "skill": (
        "OUTLOOK (web). Open https://outlook.office.com/mail. Same rules as Gmail: triage "
        "unread, draft replies into Drafts only, NEVER send/delete without explicit consent.")},
    "gcal": {"keywords": ["calendar", "schedule", "gcal", "meeting", "event", "agenda"], "skill": (
        "GOOGLE CALENDAR (web). Open https://calendar.google.com. Use Week or Schedule view "
        "to read upcoming events; report them grouped by day with times and locations, and "
        "flag overlaps. Do NOT create, move, or delete events unless explicitly asked.")},
    "github": {"keywords": ["github", "pr", "pull request", "issue", "repo", "review"], "skill": (
        "GITHUB (web). Open https://github.com/notifications for the inbox, or a repo's "
        "/pulls and /issues. Filter to items assigned to or requesting the user. Summarize "
        "title, repo, status, and what's blocking. Do NOT merge, close, or comment unless asked.")},
    "slack": {"keywords": ["slack", "channel", "dm", "message"], "skill": (
        "SLACK (web). Open https://app.slack.com. Visit unread channels/DMs and summarize the "
        "discussion (skip bot/notification channels). NEVER post a message or react unless the "
        "user explicitly asked, and read the exact text back to them before sending.")},
    "notion": {"keywords": ["notion", "wiki", "doc", "note", "page"], "skill": (
        "NOTION (web). Open https://www.notion.so. Use the search (Ctrl/Cmd+P) to find pages; "
        "open the top hit and summarize. Do NOT edit or delete pages unless explicitly asked.")},
    "drive": {"keywords": ["drive", "google drive", "file", "document", "spreadsheet"], "skill": (
        "GOOGLE DRIVE (web). Open https://drive.google.com. Search for the named file, open it, "
        "and summarize. Do NOT rename, move, share, or delete files (sharing changes access — "
        "never do it; ask the user to share manually).")},
    "youtube": {"keywords": ["youtube", "video", "transcript", "watch"], "skill": (
        "YOUTUBE. For a video URL, prefer the transcript: open the video, click '...more' then "
        "'Show transcript', or use web_fetch on the URL. Produce a tight bulleted summary with "
        "timestamps for the key points. No account actions (don't like/subscribe/comment).")},
    "spotify": {"keywords": ["spotify", "music", "song", "playlist", "play", "track"], "skill": (
        "SPOTIFY. If the desktop app is open, prefer UIA (uia_find/uia_click by control name); "
        "otherwise open https://open.spotify.com. To play: search, then click the track/playlist "
        "and the Play button. Confirm what started playing. Don't change account/library settings.")},
    "teams": {"keywords": ["teams", "microsoft teams", "channel", "chat"], "skill": (
        "MICROSOFT TEAMS (web). Open https://teams.microsoft.com. Summarize unread chats and the "
        "followed channels. NEVER post or reply unless explicitly asked.")},
    "whatsapp": {"keywords": ["whatsapp"], "skill": (
        "WHATSAPP (web). Open https://web.whatsapp.com (the phone must be linked). Summarize "
        "unread conversations. NEVER send a message unless the user explicitly asked, and read "
        "the exact text back before sending.")},
    "telegram": {"keywords": ["telegram"], "skill": (
        "TELEGRAM (web). Open https://web.telegram.org. Summarize unread chats. NEVER send a "
        "message unless explicitly asked; confirm the exact text first.")},
    "discord": {"keywords": ["discord", "server", "channel"], "skill": (
        "DISCORD (desktop app, Electron). Use UIA: focus_window 'Discord'; if uia_find returns "
        "nothing, electron_unlock it, then retry. Click the server in the left rail by name, then "
        "the channel (names look like '〔💬〕general'). Read messages from the message list. NEVER "
        "send a message unless the user explicitly asked — confirm the exact text and channel first.")},
    "linear": {"keywords": ["linear", "issue", "ticket", "sprint", "cycle"], "skill": (
        "LINEAR (web). Open https://linear.app. Use 'My Issues' to list issues assigned to the "
        "user, grouped by status; flag overdue ones. Do NOT change status or comment unless asked.")},
    "jira": {"keywords": ["jira", "ticket", "issue", "board", "sprint"], "skill": (
        "JIRA (web). Open the user's Jira board. List tickets assigned to them by status and "
        "summarize what's in progress. Do NOT transition or comment on tickets unless asked.")},
    "trello": {"keywords": ["trello", "board", "card", "list"], "skill": (
        "TRELLO (web). Open https://trello.com. Summarize the named board's cards by list. Do "
        "NOT move, archive, or edit cards unless explicitly asked.")},
    "excel": {"keywords": ["excel", "spreadsheet", "workbook", "cell", "formula", "xlsx", "sum", "chart", "pivot"], "skill": (
        "EXCEL (desktop, UIA — no screenshots). focus_window 'Excel'. The grid is a UIA "
        "DataGrid: each cell is a control named like 'B2'. To read a cell, uia_find its name and "
        "read the value. To write: uia_click the cell (or use the Name Box — uia_type 'Name Box' "
        "with 'B2' + Enter to jump there), then uia_type the value/formula and press Enter. Type a "
        "formula exactly, e.g. '=SUM(B2:B10)'. For a run of entries down a column, do them as "
        "discrete cells. Use the ribbon by name (Home, Insert, Formulas) via uia_click. Save with "
        "Ctrl+S (key_combo). NEVER overwrite a populated cell or delete a sheet without confirming.")},
    "word": {"keywords": ["word", "document", "docx", "letter", "essay", "report", "paragraph"], "skill": (
        "WORD (desktop, UIA). focus_window 'Word'. The body is an editable 'Document' control — "
        "uia_type into it to add text (it pastes, so long text is instant). Use the ribbon by name "
        "(Home, Insert, Layout) and the gallery buttons (Bold, 'Heading 1') via uia_click. For "
        "find/replace use Ctrl+H. Save with Ctrl+S. Don't close without saving; don't delete "
        "existing content unless asked.")},
    "powerpoint": {"keywords": ["powerpoint", "slide", "deck", "presentation", "pptx"], "skill": (
        "POWERPOINT (desktop, UIA). focus_window 'PowerPoint'. Add a slide via the Home ribbon → "
        "'New Slide'. Click into the title/body placeholders and uia_type the text. Switch slides "
        "in the left thumbnail rail (uia_click the slide). Use the ribbon by name. Save with "
        "Ctrl+S. Confirm slide count + titles when done.")},
    "vscode": {"keywords": ["vscode", "vs code", "code", "project", "repo", "function", "bug", "test", "build"], "skill": (
        "VS CODE / coding. PREFER the real coding tools over driving the UI: read_file, write_file, "
        "file_glob, file_grep, find_symbol, run_command/bash, run_tests, lint_code, git. Make the "
        "edit, run the tests, and report what passed. Only drive the VS Code window via UIA "
        "(electron_unlock first if uia_find is empty) when the user specifically wants the editor "
        "itself manipulated. Never commit or push unless explicitly asked.")},
    "gdocs": {"keywords": ["google docs", "gdoc", "doc", "document", "write", "draft"], "skill": (
        "GOOGLE DOCS (web). Open https://docs.google.com. Open/create the named doc; the canvas is "
        "a normal text field — type to write, use the toolbar/menus (Format, Insert) for "
        "structure. It autosaves. Do NOT use Share (it changes who can access — ask the user to "
        "share manually) and don't delete docs.")},
    "gsheets": {"keywords": ["google sheets", "gsheet", "sheet", "spreadsheet", "cell", "formula"], "skill": (
        "GOOGLE SHEETS (web). Open https://sheets.google.com. Click a cell (or use the Name Box to "
        "jump to e.g. B2), type the value/formula ('=SUM(B2:B10)') and Enter. Read values straight "
        "from the grid. It autosaves. Don't delete rows/sheets or change sharing without asking.")},
    "canva": {"keywords": ["canva", "design", "poster", "thumbnail", "graphic", "template"], "skill": (
        "CANVA (web). Open https://www.canva.com. Search templates from the home bar, click one to "
        "open the editor, double-click text elements to edit, drag/replace images from the left "
        "panel, then Share → Download to export. Confirm what you created. Don't delete the user's "
        "existing designs.")},
    "figma": {"keywords": ["figma", "design", "frame", "component", "prototype", "mockup"], "skill": (
        "FIGMA (web). Open https://www.figma.com, open the named file. Read frames/layers from the "
        "left layers panel and the right inspect panel (sizes, colors, fonts) to report design "
        "context or generate matching code. Treat it as READ-ONLY unless the user explicitly asks "
        "for edits — don't move or delete layers.")},
    "maps": {"keywords": ["maps", "directions", "route", "navigate", "distance", "address", "nearby", "restaurant near"], "skill": (
        "GOOGLE MAPS (web). Open https://maps.google.com. Search a place in the search box, or "
        "click Directions and enter origin + destination to get distance/ETA and step-by-step "
        "route. Report the options (driving/transit) with times. Read-only; don't save or share.")},
    "amazon": {"keywords": ["amazon", "buy", "product", "order", "price", "shopping", "cart"], "skill": (
        "AMAZON (web). Open https://www.amazon.com. Search the product, compare the top results on "
        "price/rating/Prime, open a listing for details. CRITICAL: never add to cart-and-checkout "
        "or place an order without the user's explicit go-ahead, and even then read the exact item "
        "+ price + total back first. For existing orders, use Returns & Orders to report status.")},
    "linkedin": {"keywords": ["linkedin", "profile", "connection", "job", "recruiter", "network", "post"], "skill": (
        "LINKEDIN (web). Open https://www.linkedin.com. Read the feed, a profile, or Jobs and "
        "summarize. NEVER post, comment, react, send connection requests, or message anyone unless "
        "the user explicitly asks — and confirm the exact text/recipient first. Treat it as "
        "read-only by default.")},
    "twitter": {"keywords": ["twitter", "tweet", "x.com", "post", "timeline", "thread"], "skill": (
        "X / TWITTER (web). Open https://x.com. Read the timeline, a profile, or search a topic and "
        "summarize. NEVER post, reply, repost, or like unless the user explicitly asks — read the "
        "exact draft back before posting.")},
    "reddit": {"keywords": ["reddit", "subreddit", "thread", "r/", "upvote", "comment"], "skill": (
        "REDDIT (web). Open https://www.reddit.com. Go to the named subreddit or search, sort by "
        "Top/Hot, and summarize the leading posts and notable comments. Don't post, comment, or "
        "vote unless explicitly asked.")},
    "todoist": {"keywords": ["todoist", "task", "todo", "to-do", "reminder", "due"], "skill": (
        "TODOIST (web). Open https://todoist.com/app. Read Today/Upcoming and project lists; report "
        "what's due or overdue, grouped by project. To add a task, use the '+ Add task' control and "
        "confirm the text/date — only when asked. Don't complete or delete tasks unless told.")},
    "calendly": {"keywords": ["calendly", "booking", "schedule", "availability", "event type", "meeting link"], "skill": (
        "CALENDLY (web). Open https://calendly.com/app. Report event types and Scheduled Events "
        "(upcoming bookings) with times and invitees. Don't change availability, cancel, or "
        "reschedule unless the user explicitly asks.")},
    "hubspot": {"keywords": ["hubspot", "crm", "contact", "deal", "pipeline", "lead", "company"], "skill": (
        "HUBSPOT (web). Open https://app.hubspot.com. Use Contacts/Companies/Deals search to find a "
        "record and summarize it, or open a pipeline and report deal stages/values. Don't create or "
        "edit records, change deal stages, or send emails unless the user explicitly asks.")},
    "filesystem": {"keywords": ["file", "folder", "directory", "downloads", "documents", "desktop"], "skill": (
        "LOCAL FILES. Use the real file tools — list_directory, read_file, write_file, file_glob, "
        "file_grep, analyze_folder. Prefer these over screenshots. Confirm before overwriting or "
        "deleting anything; never delete outside what the user asked.")},
    "clipboard": {"keywords": ["clipboard", "copied", "paste"], "skill": (
        "CLIPBOARD. Use get_clipboard to read what the user copied and set_clipboard to place a "
        "result they can paste. The clipboard contents are often the real subject of the task.")},
}


def skill_menu() -> list[tuple[str, str]]:
    """L1 (always-on) routing metadata, à la Claude / OpenClaw skills: for every
    LINKED connector, (label, when-to-use). Shown to the agent on every task so
    it knows what surfaces exist and WHEN to reach for them — the full manual is
    only loaded (L2) for the one the task actually needs (relevant_briefs)."""
    return [(c["label"], c.get("tip") or "") for c in linked_only()]


def relevant_briefs(goal: str) -> list[tuple[str, str]]:
    """For a goal, return (label, manual) for each LINKED connector whose
    keywords appear in the goal — i.e. hand the agent the manual for the tool
    it's about to use. Empty if nothing matches."""
    g = (goal or "").lower()
    out: list[tuple[str, str]] = []
    for c in linked_only():
        meta = CONNECTOR_SKILLS.get(c["id"], {})
        kws = [c["id"], (c.get("label") or "").lower()] + meta.get("keywords", [])
        if any(k and k in g for k in kws):
            skill = meta.get("skill", "")
            if skill:
                out.append((c["label"], skill))
    return out


def store_path() -> Path:
    """workspace/connectors.json"""
    return workspace_state_path("connectors.json")


def _load_state() -> dict[str, Any]:
    data = read_json(store_path(), {})
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    write_json(store_path(), state)


def list_with_state() -> list[dict]:
    """Return CONNECTORS with `linked` / `linked_at` / `notes` merged in."""
    state = _load_state()
    out = []
    for c in CONNECTORS:
        c = dict(c)
        s = state.get(c["id"], {})
        # Local connectors are implicitly linked — they need no setup.
        c["linked"] = bool(s.get("linked")) or c["auth_kind"] == "local"
        c["linked_at"] = s.get("linked_at")
        c["notes"] = s.get("notes", "")
        # Expose the connector's skill manual + whether it has one, so the
        # dashboard can show that each tool comes with a how-to for the agent.
        meta = CONNECTOR_SKILLS.get(c["id"], {})
        c["skill"] = meta.get("skill", "")
        c["has_skill"] = bool(meta.get("skill"))
        out.append(c)
    return out


def get(connector_id: str) -> Optional[dict]:
    for c in list_with_state():
        if c["id"] == connector_id:
            return c
    return None


def link(connector_id: str, notes: str = "") -> Optional[dict]:
    from datetime import datetime, timezone
    if not any(c["id"] == connector_id for c in CONNECTORS):
        return None
    state = _load_state()
    state[connector_id] = {
        "linked": True,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    _save_state(state)
    return get(connector_id)


def unlink(connector_id: str) -> Optional[dict]:
    state = _load_state()
    state.pop(connector_id, None)
    _save_state(state)
    return get(connector_id)


def linked_only() -> list[dict]:
    """For the agent context — what surfaces is it allowed to drive?"""
    return [c for c in list_with_state() if c["linked"]]
