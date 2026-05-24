# Daily CLI single snapshot pattern

For a command that reports multiple views of one valuation, build the priced
snapshot once and fan out derived payloads from that snapshot.

Do not call two public read/write entrypoints when both entrypoints internally
fetch prices. If a service bundle already owns the single-snapshot workflow,
the CLI should consume that bundle rather than recomposing separate service
calls.
