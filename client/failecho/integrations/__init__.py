"""Framework integrations.

Each integration is optional and imports its framework lazily, so the core
client keeps its zero-dependency promise. Install the framework yourself; the
integration adds nothing to FailEcho's own requirements.

Available today:

    failecho.integrations.pydantic_ai   reference integration

Everything else (LangChain, LlamaIndex, CrewAI, OpenAI Agents SDK, Claude Code
hooks) should be built against ``failecho.adapters.ToolTelemetrySink`` -- the
four-event seam -- rather than by touching FailEcho's core.
"""
