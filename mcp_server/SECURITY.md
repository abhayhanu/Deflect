# Security

This server can move money. It is written on the assumption that whatever calls it, a model, a
person in Claude Desktop or a script, may be wrong, confused or hostile. Every rule below is
enforced in code or in the database, and every one has a test in `tests/test_security.py`.

Path traversal is one of the most common flaws found in public MCP servers, so the first rule is
about it.

## 1. No tool touches the filesystem

No tool takes a path, a file name or a URL. Policies are looked up by `doc_id`, and a `doc_id`
must match `^pol_[a-z_]{2,40}$` and then exist in the `policy_docs` table, which is the allowlist.
There is no code path from a tool argument to `open()`.

*Tests:* `test_exactly_nine_tools_and_none_touch_files_config_or_logs`,
`test_path_traversal_is_refused`, `test_a_well_formed_but_unknown_policy_id_is_still_refused`,
`test_search_only_ever_returns_policy_sections`.

## 2. Every argument is bounded

Arguments are Pydantic fields, and the SDK validates them before a tool body runs.

| Kind | Bound |
| --- | --- |
| Order, customer, policy, ticket ids | A strict pattern such as `^A\d{4,6}$` |
| Idempotency key | `^[A-Za-z0-9_.:-]{8,128}$` |
| Money | `amount_inr` must be above 0, and the tool refuses anything above Rs 25,000 |
| Counts | `limit` 1 to 50, `top_k` 1 to 10 |
| Choices | Closed lists: `reason_code`, `reason`, `intent`, `priority` |
| Free text | Length limits: search query 4,000, address 200, escalation summary 1,000 characters |

Free text never shapes a query. The search query goes to the embedding model, and the address and
the summary are stored as bound parameters.

An argument the tool does not declare is refused with `unknown_argument`, before the tool body
runs. The SDK on its own drops unknown arguments silently, and in the v2 eval run the agent sent
`cancel_order` a `reason_code` it believed would be used. Every published schema also says
`additionalProperties: false`, so a client can see the rule before it calls.

*Tests:* `test_every_argument_is_bounded`, `test_free_text_is_stored_as_data_never_run`,
`test_an_argument_the_tool_does_not_declare_is_refused`, `test_every_published_schema_forbids_extra_arguments`.

## 3. All database access is parameterised

Every query uses `%s` placeholders with the values passed separately. No SQL is built by joining
strings. The test sends `A1234' OR '1'='1'; DROP TABLE orders; --` into every string argument
of every tool and then checks that no table changed size.

*Test:* `test_sql_injection_in_every_string_field`.

## 4. Idempotency is enforced by the database

Every write tool starts its transaction by inserting its `idempotency_key` into `tool_requests`,
whose primary key is that column. A second request with the same key fails on the unique index,
the transaction rolls back, and the caller gets `duplicate_request` along with the earlier result.

There is deliberately no "check if the key exists, then insert" step in Python. Two identical
requests arriving together would both pass such a check. The database cannot be raced. A test
fires six identical refunds at once and asserts that exactly one refund row exists afterwards.

A request that is rejected for any other reason rolls back its key too, so a fixed request can be
sent again with the same key.

*Tests:* `test_duplicate_idempotency_key_is_refused`, `test_the_database_itself_enforces_the_key`,
`test_simultaneous_duplicates_refund_once`, `test_a_rejected_call_does_not_use_up_its_key`.

## 5. A refund must cite a real policy

`issue_refund` has a required `policy_doc_id` argument. The server checks it against
`policy_docs` inside the refund transaction, and the `refunds` table also has a foreign key to
`policy_docs`. A refund that no policy backs cannot be made through this server at all. It is
not a prompt asking the model to behave, it is the shape of the protocol.

The other refund rules are enforced here too: never more than `total_inr` minus `refunded_inr`,
never above Rs 25,000, never a `lost_in_transit` refund while the delivery scan is under 48 hours
old, and never a `lost_in_transit` refund on a parcel that is still on its way and not more than
7 days past its promised date. The last rule was added after the v2 eval run, where the agent
refunded Rs 3,000 for an order that tracking showed in transit and on time.

*Tests:* `test_refund_rejections`, `test_partial_refund_respects_what_was_already_refunded`.

## 6. No tool reads the server's own config, environment or logs

There are exactly nine tools and none of them returns settings, environment variables or log
lines. Errors from bugs are returned as `Error executing tool <name>` with no detail, while the
full traceback stays in the server's own log. Only deliberate rejections, like `not_found` or
`exceeds_refundable`, send their message back.

The clock is the one setting that changes behaviour. `DEFLECT_CLOCK=seed_anchor` makes the server
use the seed time as now, which evals need. Only the process that starts the server can set it.
No tool argument can change the time.

*Test:* `test_exactly_nine_tools_and_none_touch_files_config_or_logs`.

## 7. Transport

**stdio** is for local use. Whoever starts the process owns it, so it has full access. The agent
passes the server only the database and Qdrant settings, never the model API keys.

**HTTP** needs a bearer token on every request and refuses to start without `MCP_HTTP_TOKEN`.

| Token | Can list | Can call |
| --- | --- | --- |
| `MCP_HTTP_TOKEN` | all nine tools | all nine tools |
| `MCP_HTTP_READONLY_TOKEN` | the four read tools | the four read tools |
| missing or wrong | nothing, `401` | nothing, `401` |

The read only token is meant for a public demo. Tokens are compared in constant time.

*Tests:* `test_full_token_sees_every_tool`, `test_read_only_token_sees_and_calls_only_read_tools`,
`test_no_valid_token_no_entry`, `test_gate_answers_401_before_any_mcp_code_runs`,
`test_http_refuses_to_start_without_a_token`.

## 8. The server stands alone

Nothing in the server imports the Deflect agent, its data scripts or its evals. It depends on
the database, not on the code around it.

*Test:* `test_mcp_server_depends_on_the_database_not_on_deflect`, in the main repo's
`agent/test_contracts.py`, which CI runs on every push.

## Known limits

- Bearer tokens are static strings from the environment. A public deployment should put the
  server behind TLS and rotate tokens, or move to the OAuth support in the MCP SDK.
- The server does not check which customer is calling. Over stdio the caller is trusted, and the
  agent checks ownership before it acts. A multi tenant deployment would need the customer
  identity carried in the token.
- Rules that need judgement, such as whether an item category can be returned, are left to the
  agent and its guardrails. The server enforces the facts: amounts, status, time windows and one
  of a kind actions.

## Reporting a problem

Open an issue on the repository, or email the maintainer for anything that should not be public.
