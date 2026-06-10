# flex labs

Experimental modules and skills for [flex](https://github.com/damiandelmas/flex)
— the SQLite knowledge and memory engine for AI agents.

**The contract, in one line:** everything here works for us, installs over a
stable flex, and may change or break without notice. Issues and field reports
are the whole point. No support promise.

## How labs relates to flex

```
CORE        ships in the flex wheel — actively developed
COMMUNITY   ships in the wheel, beta tier — CI-tested, issue-driven fixes
LABS        this repo — install over the wheel, experimental
```

Labs is where things earn their way into the wheel. The graduation bar is
real-world execution: a labs module that accumulates field reports and passes
the cold-start test matrix moves to the community tier in a flex release.

## Install

Requires flex >= 0.31 (`curl -sSL https://getflex.dev/install.sh | bash`).

flex discovers external modules in `~/.flex/modules/`. Installing a labs
module is a copy:

```bash
git clone https://github.com/damiandelmas/flex-labs
cd flex-labs
./scripts/install-module.sh devto      # copies modules/devto -> ~/.flex/modules/devto
flex init --module devto --tags python,ai
```

Skills install the same way to your agent's skills directory:

```bash
cp -r skills/flex-topology ~/.claude/skills/
cp -r skills/flex-archeology ~/.claude/skills/
```

## What's here

### modules/

| module | what it indexes | one-liner |
|---|---|---|
| `devto` | Dev.to articles + comments by tag/author (no auth) | `flex init --module devto --tags python,ai` |
| `lobsters` | lobste.rs stories + comments by tag (no auth) | `flex init --module lobsters --lobsters-tags programming,ai` |
| `agents/aider` | Aider chat histories — programmable memory for Aider | `flex init --module aider` |
| `agents/opencode` | OpenCode SQLite sessions — programmable memory for OpenCode | `flex init --module opencode` |

Every module cell self-documents: run `@orient` on it and the module's own
instructions, views, and presets come back as part of the answer. Known gaps
are documented per module — read them; they're honest.

### skills/

| skill | what it does |
|---|---|
| `flex-topology` | standup-style map of your recent work — workstreams, hubs, what's hot, what went quiet — mined from your own coding-agent sessions |
| `flex-archeology` | project history reconstruction from session evidence: file lineage, workstream recovery, artifact genealogy |

Both expect `claude_code` and/or `codex` cells (the flex coding-agent install).

## Reporting

Open an issue with: the command you ran, what you expected, what happened, and
`flex status` output. A failed experiment reported well is a contribution.

## License

MIT, same as flex core.
