# deflect-mcp

An MCP server for the support team of an online shop. It exposes nine tools that read orders and
policies and take real actions, and it is built so that an AI agent cannot do something the
database does not allow: every write is idempotent, and a refund must cite a real policy.

It is the tool layer of [Deflect](../README.md), a support operations agent, but it has no code
dependency on the agent. Any MCP client can use it, including Claude Desktop.

## Tools

| Tool | Class | Changes data | What it does |
| --- | --- | --- | --- |
| `get_order` | read | no | One order with items, amounts, shipments and refunds |
| `get_customer_history` | read | no | Recent orders and every refund for a customer |
| `search_policy` | read | no | Semantic search over the policy sections, filtered by intent. A pinned policy is always in the answer |
| `check_shipment` | read | no | Carrier, tracking number, latest event and promised date |
| `update_shipping_address` | write_low | yes | Same city only, before the order ships |
| `create_return_label` | write_low | yes | Pickup for a delivered order, within 7 days |
| `cancel_order` | write_low | yes | Before shipping, refunds prepaid orders automatically |
| `issue_refund` | write_high | yes | Needs a `policy_doc_id` that names a real policy |
| `escalate_to_human` | write_low | yes | Opens a case in the human support queue |

Every write takes an `idempotency_key`. Every published schema sets `additionalProperties` to
false, and a call with an argument the tool does not declare is refused rather than quietly
ignored. The class is published in each tool's `_meta` as
`deflect/risk`, and the standard MCP hints are set too: `readOnlyHint`, `destructiveHint` and
`idempotentHint`.

When a write is refused, the error starts with a short code the caller can act on:

| Code | Meaning |
| --- | --- |
| `not_found` | No such order |
| `wrong_status` | The order is in a status where this is not possible |
| `different_city` | The new address is in another city |
| `needs_verification` | Orders above Rs 25,000 need an identity check first |
| `outside_window` | The 7 day return window has closed |
| `already_returned` | The order already has a return |
| `over_ceiling` | A refund above Rs 25,000 |
| `ungrounded` | `policy_doc_id` is not a real policy |
| `exceeds_refundable` | More than `total_inr` minus `refunded_inr` |
| `too_early` | A lost in transit claim before the 48 hour wait |
| `not_lost` | A lost in transit claim on a parcel that is not delivered and not more than 7 days late |
| `unknown_argument` | The call carried an argument the tool does not declare, nothing was done |
| `duplicate_request` | The idempotency key was already used, nothing was done again |
| `forbidden` | The read only token tried a write tool |

## Run it

It needs the Deflect Postgres database and the Qdrant policy index. From the Deflect repo root:

```bash
docker compose up -d
python -m data.seed_orders --reset
python -m data.index_policies

python -m mcp_server.server --transport stdio
MCP_HTTP_TOKEN=change-me python -m mcp_server.server --transport http --port 8931
```

Over HTTP the endpoint is `http://127.0.0.1:8931/mcp`. Set `MCP_HTTP_READONLY_TOKEN` as well to
hand out a token that can only see and call the read tools.

| Setting | Default | Used for |
| --- | --- | --- |
| `DATABASE_URL` or `POSTGRES_*` | `localhost:5434`, user, password and database `deflect` | Orders and writes |
| `QDRANT_URL`, `QDRANT_COLLECTION` | `http://localhost:6333`, `deflect_policies` | Policy search |
| `QDRANT_API_KEY` | none | Only for a hosted Qdrant |
| `DEFLECT_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Must match the model the index was built with |
| `MCP_HTTP_TOKEN`, `MCP_HTTP_READONLY_TOKEN` | none | HTTP access |
| `DEFLECT_CLOCK` | real time | `seed_anchor` pins now to the seed time, for evals |

## Use it from Claude Desktop

Open Claude Desktop, go to Settings, Developer, Edit Config. On Windows that file is
`%APPDATA%\Claude\claude_desktop_config.json`. Add this, with your own paths, then fully quit and
restart Claude Desktop:

```json
{
  "mcpServers": {
    "deflect-support": {
      "command": "E:\\Deflect\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server.server", "--transport", "stdio"],
      "env": { "PYTHONPATH": "E:\\Deflect", "POSTGRES_PORT": "5434" }
    }
  }
}
```

Then ask Claude something like "Use deflect-support to look up order A8842."

## Tests

```bash
pytest mcp_server
```

The tests create and seed their own database, your database name with `_test` added, so your
dev data is never touched. They need Postgres running. `tests/test_security.py` is the evidence
for every claim in [SECURITY.md](SECURITY.md).

## Publishing it on its own

The folder has its own `pyproject.toml` and builds a wheel called `deflect-mcp` with a
`deflect-mcp` command. To move it into its own repository, copy this folder, copy
`data/schema.sql` from Deflect for the tables, and keep the tests' seed step pointing at a
seeded database.

## License

MIT
