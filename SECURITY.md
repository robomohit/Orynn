# Security Policy

Orynn can read local context, call LLM providers, run code, browse the web,
and, on Windows, control desktop applications through UI Automation. Treat it as
powerful local automation software, not as a sandbox boundary.

## Supported Versions

Security fixes are handled on the default public branch. If you publish packaged
releases, document which release line is currently supported in the release notes.

## Reporting a Vulnerability

Please do not open a public issue for exploitable vulnerabilities or leaked
secrets. Report privately to the repository owner with:

- A short description of the issue and affected feature.
- Reproduction steps or a proof of concept.
- Impact, including whether it can read files, run commands, exfiltrate data, or
  bypass user approval.

Expected response target: acknowledgement within 72 hours, followed by triage,
fix, and disclosure timing based on severity.

## Public Deployment Guidance

- Do not expose the dashboard to the public internet. Run it on localhost or a
  trusted private network only.
- Keep `.env`, runtime logs, screenshots, clipboard history, and task state out
  of git. The repository `.gitignore` is configured for this; review `git status`
  before publishing.
- Use a dedicated low-privilege API key for each LLM provider.
- Review commands, file edits, browser actions, and desktop actions before
  approval when running with elevated autonomy.
- Be careful with desktop-control tasks that involve password managers, banking,
  private messages, source code, customer data, or administrative consoles.

## Untrusted Web Content

Agentic browser and desktop tools must treat web pages, search snippets, and
accessibility trees as untrusted data. Orynn wraps fetched web content with
an explicit untrusted-content boundary before passing it to the model, and the
test suite covers that behavior. Still, users should avoid approving sensitive
actions that are justified only by instructions found on a web page, email,
document, or other external content.

## Security Scope

Security-sensitive areas include:

- Local API authentication and session handling.
- Approval/permission gates for file, shell, browser, and desktop actions.
- SSRF and URL-fetch protections.
- Clipboard, screenshot, memory, task-log, and automation persistence.
- Integrations that use third-party tokens, such as Discord, Telegram, Slack, or
  MCP servers.

Known non-goals: this project does not attempt to isolate malicious local users
from the same OS account, and it is not a hardened multi-tenant service.
