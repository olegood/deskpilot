"""Paywisp's OAuth 2.1 authorization server.

Hand-built on joserfc so every step of the protocol is visible (see Deskpilot's
docs/decisions.md, D-010 and D-137). Only what Paywisp needs is implemented: this
step adds the client credentials grant, discovery metadata, and the key set.
"""
