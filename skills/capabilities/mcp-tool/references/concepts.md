# Concepts — optimizing an external MCP toolset

> The mental model behind the `mcp-tool` capability: what the Model Context
> Protocol is, who owns which part of a tool, why only a safe edit subset is
> permitted, and why external tool metadata is untrusted. Grounded in the
> official MCP specification and security guidance.

## Contents
- 1. What MCP is
- 2. The Tool object — what the model sees
- 3. The ownership boundary (why the policy is restricted)
- 4. External tool metadata is untrusted
- 5. Practical optimization playbook
- Sources

## 1. What MCP is

The Model Context Protocol is an open standard for connecting AI applications to
external systems — "a USB-C port for AI." It defines a **client–server**
architecture over JSON-RPC 2.0:

- **MCP Host** — the AI application that coordinates one or more clients.
- **MCP Client** — a connection to one MCP server.
- **MCP Server** — a program that *provides context* to clients, exposing three
  primitives: **Tools**, Resources, and Prompts.

The host fetches the available tools from all connected servers, "combines them
into a unified tool registry that the language model can access," and the model
"automatically generates the appropriate tool calls." That is the same
select-then-fill loop as native function calling — the model sees tool
definitions and chooses one.

## 2. The Tool object — what the model sees

A server exposes tools via the `tools/list` request and they are invoked via
`tools/call`. Each **Tool** object's fields (quoted from the 2025-06-18 spec):

- `name` — "Unique identifier for the tool"
- `title` — "Optional human-readable name… for display purposes"
- `description` — "Human-readable description of functionality"
- `inputSchema` — "JSON Schema defining expected parameters"
- `outputSchema` — "Optional JSON Schema defining expected output structure"
- `annotations` — "optional properties describing tool behavior"

The model selects from `name` + `description` and fills arguments from
`inputSchema`. Tools are explicitly **"model-controlled."** This is why, on the
client side, the only things that move selection/filling are the *description*
and any *examples* you surface — and why a clear description matters as much here
as for native tools.

## 3. The ownership boundary (why the policy is restricted)

The server **owns** the implementation and the `inputSchema`. The host/client
**decides which tools to expose** to the model and can filter or annotate the
presentation. Mapped to this capability's actions:

| Edit | Who owns it | Allowed in `mcp-tool`? |
|------|-------------|:--:|
| `description`, per-param description, examples | client presentation | yes |
| which tools the model sees (`add`/`remove`) | client/host curation | yes |
| `inputSchema` (types, required, enums) | **server** | no |
| handler `code` / behavior | **server** | no |
| a new composite that runs code | needs server code | no (compose agent-side via `tools`) |

A server can also change its tool list at runtime and emit
`notifications/tools/list_changed`; treat `add`/`remove` as *your curation* of the
available set, not a change to the server.

## 4. External tool metadata is untrusted

Because the description and schema come from a third party, they are **untrusted
input to the model**:

- **Tool poisoning** — a server can embed hidden instructions in a tool
  `description` that "are invisible to users but fully readable by AI models."
  The model acts on them; the user, who sees a simplified UI, never knows.
- **Shadowing** — a malicious server's tool description can alter how the model
  uses *other, trusted* tools.
- **list_changed abuse** — the spec's security annex describes a "Session Hijack
  Prompt Injection" that abuses `notifications/tools/list_changed` to enable tools
  the user wasn't aware of.

The spec itself carries a warning that clients **MUST** treat tool annotations as
untrusted unless they come from a trusted server. Operational implication for this
capability: **review every description you expose**, prefer well-known/trusted
servers, and remove tools whose metadata you can't vouch for.

## 5. Practical optimization playbook

1. **Re-describe terse server tools** on the client side — state what/when/when-not
   and the real return shape. This is the highest-leverage edit (selection).
2. **Annotate parameter descriptions** to pin formats/limits the schema implies
   but doesn't spell out — without changing `type`/`required` (those are `schema`,
   forbidden here).
3. **Curate the exposed set** — hide overlapping/legacy tools so the needed ones
   stand out; selection degrades as the set grows.
4. **Never document capabilities the server lacks** — overpromising causes
   confident wrong calls.
5. If you need a real schema constraint or new behavior, that's a server change or
   an agent-owned wrapper in [`tools`](../../tools/SKILL.md) — not an `mcp-tool` edit.

## Sources

- MCP Specification — Tools (Tool object fields `name`/`description`/`inputSchema`,
  `tools/list`, `tools/call`, model-controlled, `listChanged`, annotations
  untrusted): https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- MCP — Architecture overview (host/client/server roles; unified tool registry;
  JSON-RPC): https://modelcontextprotocol.io/docs/learn/architecture
- MCP — Tools concept page (version-independent field definitions): https://modelcontextprotocol.io/docs/concepts/tools
- MCP — Introduction ("USB-C port for AI"; open standard): https://modelcontextprotocol.io/introduction
- Anthropic — Introducing the Model Context Protocol (Nov 25, 2024; server/client
  split): https://www.anthropic.com/news/model-context-protocol
- MCP spec/schema repository (canonical `Tool` interface in TS + JSON Schema): https://github.com/modelcontextprotocol/modelcontextprotocol
- MCP — Security Best Practices (confused deputy, token passthrough, session
  hijack via list_changed): https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices
- Invariant Labs — MCP Tool Poisoning Attacks (hidden instructions in
  descriptions; shadowing): https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
