"""The HTTP layer.

Everything here is a thin shell over services that already exist and are already
tested. A route reads the request, calls a service, and shapes the response; the
decisions were made in deskpilot.auth and deskpilot.authz.
"""
