# Claude Code Plugin / MCP Stack

Date reviewed: 2026-09-09.

Principle: install only tools that remove a current bottleneck. Plugins expand Claude Code's authority and attack surface; prefer maintained sources and minimal permissions.

Anthropic's official plugin directory is:
`anthropics/claude-plugins-official`

Typical installation:
```text
/plugin marketplace add anthropics/claude-plugins-official
/plugin install <plugin-name>@claude-plugins-official
```

Use `/plugin > Discover` if availability/installation syntax changes.

---

## Tier 1 — Install at project start

### 1. `claude-code-setup`
Priority: HIGH

Why:
- Anthropic-maintained recommender for hooks, skills, MCP and subagents;
- read-only analysis of the repo;
- useful after the initial scaffold exists.

Install:
```text
/plugin install claude-code-setup@claude-plugins-official
```

Use after Phase 0 scaffold:
“Recommend the smallest useful Claude Code automation set for this repository. Do not add tools that are not necessary for Phase 0.”

### 2. `claude-md-management`
Priority: HIGH

Why:
- helps audit and maintain `CLAUDE.md`;
- important because the project will evolve across many sessions;
- prevents root instructions becoming a huge stale notebook.

Install:
```text
/plugin install claude-md-management@claude-plugins-official
```

### 3. `github`
Priority: HIGH once the repo is on GitHub

Why:
- official GitHub MCP;
- repository/issue/PR/search/API operations;
- useful for backlog, PR review and CI workflow.

Install:
```text
/plugin install github@claude-plugins-official
```

Security:
- use least-privilege auth;
- do not grant admin/repository scopes that are not needed;
- never put PATs in committed config.

For pure local development, standard `git` CLI is enough before GitHub integration is needed.

### 4. `context7`
Priority: HIGH

Why:
- current/version-specific library documentation;
- useful for FastAPI, Pydantic, FFmpeg wrappers, database libraries and platform SDKs;
- reduces hallucinated/stale API usage.

Install:
```text
/plugin install context7@claude-plugins-official
```

Note:
Context7 is third-party (Upstash) but is listed through the official Anthropic plugin directory. Keep sensitive proprietary data out of unnecessary documentation queries.

Troubleshooting:
There have been 2026 reports of plugin/MCP connection issues on some environments. If it fails, use current Claude Code MCP documentation and a manually configured Context7 MCP rather than hacking cached plugin files.

### 5. `playwright`
Priority: HIGH once any review UI exists; LOW in the first CLI-only days

Why:
- Microsoft-maintained browser automation;
- end-to-end tests for operator dashboard;
- deterministic page interaction using structured accessibility state.

Install:
```text
/plugin install playwright@claude-plugins-official
```

It can also be added directly as MCP:
```bash
claude mcp add playwright npx @playwright/mcp@latest
```

Do not use Playwright as the core source-ingestion strategy. It is for testing/controlled browser workflows, not a substitute for official platform APIs.

### 6. `security-guidance`
Priority: HIGH

Why:
- catches common vulnerabilities and secret/security mistakes during Claude-generated code changes;
- especially relevant once OAuth, uploads, file processing and web endpoints appear.

Install if not already enabled in your Claude Code environment:
```text
/plugin install security-guidance@claude-plugins-official
```

Use least privilege. Review automated findings; they are not proof of absence of vulnerabilities.

### 7. `code-review`
Priority: MEDIUM/HIGH

Why:
- second-agent review on meaningful changes;
- helpful before merging changes to rights gates, publishing, authentication, media command construction and migrations.

Install:
```text
/plugin install code-review@claude-plugins-official
```

---

## Tier 2 — Add when staging/operator UI exists

### `sentry`
Priority: HIGH at staging

Purpose:
- production/staging errors;
- stack traces;
- issue investigation from Claude Code.

Do not add before there is a deployed runtime worth monitoring.

### `posthog`
Priority: MEDIUM/HIGH

Purpose:
- product/operator analytics;
- experiments/feature flags;
- useful for dashboard behavior and B2B product later.

Important:
VME's actual content-performance dataset must remain first-party structured domain data. PostHog should complement it, not become the canonical media experiment store.

### Microsoft PostgreSQL MCP
Priority: MEDIUM, optional

Repository:
`microsoft/postgres-mcp`

Why:
- maintained database inspection tooling;
- useful for schema/query investigation.

Security:
- use a dedicated read-only DB role/profile for autonomous inspection;
- never point an unrestricted agent at production write credentials unless there is a specific controlled reason.

Do not use the old archived `@modelcontextprotocol/server-postgres` blindly. Prefer a currently maintained server when an MCP is actually needed.

---

## Tier 3 — Add when publishing/multi-platform distribution begins

### `postiz`
Priority: POTENTIALLY HIGH LATER

The official Anthropic directory currently lists Postiz as a social-media automation plugin supporting scheduling, integrations, media upload and analytics across many platforms, including YouTube, TikTok and Instagram.

Why it could be valuable:
- faster multi-platform prototype;
- avoids building every connector immediately;
- scheduling and operational distribution layer.

Why not Day 1:
- publishing is intentionally human-gated;
- core YouTube experiment should first use a controlled official adapter;
- a third-party distribution layer adds another credential/security/failure domain.

Decision rule:
Use Postiz only after VME's publication ledger and approval gate exist. It should be an adapter behind VME, never the source of truth.

### Native YouTube Data API
This is not a Claude Code plugin; it belongs in the product.

Recommendation:
Use the official YouTube API for metadata/upload/analytics flows where supported instead of relying on an unofficial “YouTube MCP” as critical infrastructure.

Google changed YouTube API quota structure in 2026. Build quota handling as configuration and inspect current documentation at implementation time.

---

## Tier 4 — Optional development acceleration

### `feature-dev`
Useful for larger feature design/exploration/review once the repo becomes substantial.

### `commit-commands`
Convenient Git commit/push/PR workflows. Nice to have, not architectural.

### `pr-review-toolkit`
Useful once PR volume rises or more collaborators join.

### `claude-security`
Deep Anthropic security scanning plugin. Run before exposing upload/auth/publishing services or on security-sensitive changes. Heavier than continuous `security-guidance`, so use deliberately.

### `plugin-dev` / `skill-creator`
Do not install at the start unless needed.

Later use case:
Create a VME-specific Claude Code plugin/skill pack containing:
- ranking evaluation workflow;
- render QA;
- rights-policy audit;
- release checklist;
- dataset regression benchmark.

---

## Plugins/MCP I would NOT make foundational

### Unofficial YouTube downloader/scraper MCP
Reason:
- rights/ToS ambiguity;
- maintenance risk;
- credential/security risk;
- a critical ingestion path should be explicit application code with policy gates.

### Generic filesystem MCP
Claude Code already works inside the repo. Add extra filesystem authority only if a clear external directory workflow requires it.

### Autonomous “infinite coding loop” plugins
Avoid in the early project. The main risk is not insufficient code generation; it is building the wrong architecture extremely fast.

### Write-enabled production database MCP
Avoid by default.

---

## Recommended Day-1 set

Install:
```text
claude-code-setup
claude-md-management
context7
security-guidance
code-review
```

Add `github` once the repository is remote.

Add `playwright` when a UI/browser workflow appears.

That is enough. More plugins do not make the MVP better.

---

## Claude Code mechanisms beyond plugins

Use native mechanisms before adding third-party complexity:

### `CLAUDE.md`
Persistent project rules. Keep it concise.

### Project docs
Read only the relevant detailed file when working on that subsystem.

### Subagents
Later create specialized agents for:
- architecture;
- rights/compliance;
- media pipeline;
- ranking;
- fact checking;
- QA/security.

Do not create 12 agents before the code exists.

### Hooks
Good future uses:
- block edits to `.env`;
- run formatter/linter after edits;
- run unit tests for touched modules;
- warn on dangerous shell patterns;
- reject accidental commits of large media/secrets.

Hooks execute shell commands with user permissions. Only install/review trusted hooks.

### Skills
Once workflows stabilize, package repeatable VME procedures as project skills rather than making `CLAUDE.md` enormous.

---

## Primary references

Anthropic official plugin directory:
https://github.com/anthropics/claude-plugins-official

Anthropic Claude Code advanced patterns:
https://www.anthropic.com/webinars/claude-code-advanced-patterns

GitHub official MCP:
https://github.com/github/github-mcp-server

Microsoft Playwright MCP:
https://github.com/microsoft/playwright-mcp

Microsoft PostgreSQL MCP:
https://github.com/microsoft/postgres-mcp

Anthropic Agent Skills:
https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
