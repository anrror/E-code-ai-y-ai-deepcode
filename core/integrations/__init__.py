"""First-class integrations with systems outside the workspace.

Each integration owns its credential boundary, transport limits and error
translation. The agent reaches them through native tools; the CLI reaches them
through the same service so both surfaces behave identically.
"""
