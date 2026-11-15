# Task-scoped MCP integration

This opt-in adapter is launched once per task by a trusted host:

```sh
python -m colony.task_mcp --home /private/identity --repo /project --ns team --question "Read the default in settings.py" --direct-path settings.py
```

The host supplies `--direct-path` only for an explicit direct-file request, not a
keyword guess or an instruction embedded in a source. Without it the adapter
uses research guidance. The model sees two small tools: `read` and `research`.
Reading uses trusted evidence references and makes no Colony lookup or push.
`research` looks up the original bound task question. Other paths can always be
read; direct routing never prevents deeper research, contradictions, examples or
requested answer detail. The host's other authorized research tools remain usable.

This integration does not intercept arbitrary tools or change a desktop host's
task dispatcher. Hosts must launch it with the actual task question and route
their explicit direct reads to it. Existing MCP/setup connections are unchanged;
do not configure both ordinary and task MCP for the same operations. Read-only
and Git repository bindings are supported. Each process must serve one task and
one principal; restart for another task. No cross-task evidence references persist.

This first adapter exposes no publication tool; conditional publication is the
next separate change. It makes no additional model calls and adds no answer cap.
Legacy lookup representation is retained on main; this adapter does not copy
PR15's adaptive packing or change retrieval. End-to-end gross savings are unproven.
