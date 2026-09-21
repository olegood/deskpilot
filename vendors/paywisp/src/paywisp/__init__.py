"""Paywisp: a fictional payment processor.

Not part of Deskpilot. Two services live here, and they are deliberately separate
processes even though they share a project:

- ``paywisp.auth_server`` issues OAuth access tokens
- ``paywisp.mcp_server`` holds the payments and checks those tokens

The MCP server learns the signing keys the way any resource server would, by
fetching the authorization server's JWKS over HTTP. Nothing is shared in memory,
which is what keeps "replace the authorization server with Keycloak" a
configuration change rather than a rewrite.
"""
