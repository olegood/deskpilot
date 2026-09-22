"""Paywisp's MCP server: the payments, behind scope-checked tools.

A resource server in OAuth terms. It never sees a password or a client secret; it
sees a bearer token, checks it against the authorization server's published keys,
and decides per tool whether the token's scopes allow the call.
"""
