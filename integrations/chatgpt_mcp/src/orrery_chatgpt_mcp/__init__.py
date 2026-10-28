"""ChatGPT-facing MCP gateway for ORRERY.

A separate process that speaks MCP Streamable HTTP, accepts only OAuth 2.1
access tokens issued for it, and reaches the local dashboard over loopback
through a fixed allowlist of read paths plus one gated spawn path. It never
proxies ORRERY Mail and never holds an agent owner token.
"""

__version__ = "0.1.0"
