# Claude — Idiograph

Session bootstrap lives in the vault. Paste the contents of
`projects/idiograph/custom-instructions.md` into the claude.ai project
custom instructions field before starting a session.

Current bootstrap:

> You are on the Idiograph project of the Idiograph ecosystem.
> The vault lives at vault.theidiograph.com via the vault-mcp connector.
>
> At session start, vault_read("protocol/VAULT-ORIENTATION.md")
> and follow its instructions.
>
> If vault-mcp is unreachable, say so and ask the user for context
> rather than proceeding from stale assumptions.
