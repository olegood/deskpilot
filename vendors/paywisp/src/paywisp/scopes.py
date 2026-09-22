"""The scopes a Paywisp token can carry.

Shared by both services because they are part of the published contract: the
authorization server lists them in its metadata, and the MCP server checks them.
Keys are what the two must never share, and they don't (D-138).
"""

# What a token can let its holder do. Read and write are separate scopes, and
# separate clients are allowed them: the question is not only "what did this client
# ask for" but "what could it ever be given".
SCOPE_PAYMENTS_READ = "payments:read"
SCOPE_REFUNDS_WRITE = "refunds:write"
ALL_SCOPES = (SCOPE_PAYMENTS_READ, SCOPE_REFUNDS_WRITE)
